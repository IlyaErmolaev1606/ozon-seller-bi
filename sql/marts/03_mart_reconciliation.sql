-- Сверка 1. API (начисления + отправления) vs отчёт о реализации, SKU × месяц.
create or replace table mart_recon_realization as
with api as (
    select coalesce(s.month, r.month) as month, coalesce(s.sku, r.sku) as sku,
           coalesce(s.qty_sold, 0) as api_qty_sold, coalesce(s.amount_sold, 0) as api_amount_sold,
           coalesce(r.qty_returned, 0) as api_qty_returned, coalesce(r.amount_returned, 0) as api_amount_returned
    from (
        select month, sku, count(*) as qty_sold, sum(amount) as amount_sold
        from stg_accrual_lines where type_id = 1 group by all
    ) s
    full join (
        select month, sku, count(*) as qty_returned, -sum(amount) as amount_returned
        from stg_accrual_lines where type_id = 14 group by all
    ) r on r.month = s.month and r.sku = s.sku
)
select coalesce(a.month, rep.month) as month, coalesce(a.sku, rep.sku) as sku,
       a.api_qty_sold, rep.qty_sold as report_qty_sold,
       coalesce(rep.qty_sold, 0) - coalesce(a.api_qty_sold, 0)                 as diff_qty_sold,
       a.api_amount_sold, rep.amount_sold as report_amount_sold,
       round(coalesce(rep.amount_sold, 0) - coalesce(a.api_amount_sold, 0), 2) as diff_amount_sold,
       a.api_qty_returned, rep.qty_returned as report_qty_returned,
       coalesce(rep.qty_returned, 0) - coalesce(a.api_qty_returned, 0)         as diff_qty_returned,
       round(coalesce(rep.amount_returned, 0) - coalesce(a.api_amount_returned, 0), 2) as diff_amount_returned,
       case when abs(coalesce(rep.qty_sold, 0) - coalesce(a.api_qty_sold, 0)) > p.tolerance_realization_qty
              or abs(coalesce(rep.qty_returned, 0) - coalesce(a.api_qty_returned, 0)) > p.tolerance_realization_qty
              or abs(coalesce(rep.amount_sold, 0) - coalesce(a.api_amount_sold, 0)) > p.tolerance_realization_rub
              or abs(coalesce(rep.amount_returned, 0) - coalesce(a.api_amount_returned, 0)) > p.tolerance_realization_rub
            then 'Расхождение' else 'OK' end as status
from api a
full join (select month::varchar as month, sku, qty_sold, amount_sold, qty_returned, amount_returned
           from raw_realization_report) rep
  on rep.month = a.month and rep.sku = a.sku
cross join params p;

-- Сверка 2. Реклама: финансы (сколько списано) vs Performance API (на что потрачено).
create or replace table mart_recon_ads as
with fin as (
    select date, -sum(amount) as fin_spend from stg_accrual_lines where pnl_line = 'ads' group by all
),
perf as (
    select date::date as date, sum(spend) as perf_spend from raw_performance_ads group by all
)
select strftime(f.date, '%Y-%m') as month,
       round(sum(f.fin_spend), 2)                                  as finance_ads,
       round(sum(coalesce(p.perf_spend, 0)), 2)                    as performance_ads,
       round(sum(coalesce(p.perf_spend, 0)) - sum(f.fin_spend), 2) as diff_rub,
       (sum(coalesce(p.perf_spend, 0)) - sum(f.fin_spend)) / nullif(sum(f.fin_spend), 0) as diff_pct,
       string_agg(case when p.perf_spend is null then strftime(f.date, '%d.%m') end, ', ' order by f.date)
                                                                   as days_missing_in_performance,
       case when abs(sum(coalesce(p.perf_spend, 0)) - sum(f.fin_spend)) / nullif(sum(f.fin_spend), 0)
                 > max(pr.tolerance_ads_pct) then 'Расхождение' else 'OK' end as status
from fin f
left join perf p using (date)
cross join params pr
group by all
order by month;
