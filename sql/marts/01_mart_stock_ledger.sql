-- Баланс остатков на складах Ozon: остаток(вчера) + приёмка + возвраты в сток − отгрузки = остаток(сегодня).
-- Всё, что не объясняется движениями, — кандидат в утрату/излишек и в претензию к площадке.
create or replace table mart_stock_ledger as
select
    date, sku, cluster, actual_qty, qty_out, qty_accepted, qty_restocked,
    lag(actual_qty) over w + qty_accepted + qty_restocked - qty_out     as expected_qty,
    actual_qty - (lag(actual_qty) over w + qty_accepted + qty_restocked - qty_out) as unexplained_qty
from stg_stock_moves
window w as (partition by sku, cluster order by date);

-- Утраты с оценкой: по себестоимости и по договорной формуле лимита возмещения
-- (действительная стоимость × коэффициент возмещения / (1 + НДС)), сравнение с полученным возмещением.
create or replace table mart_stock_losses as
with losses as (
    select sku, cluster, date, -unexplained_qty as lost_qty
    from mart_stock_ledger
    where unexplained_qty < 0
),
price7 as (
    select l.sku, l.date, avg(p.buyer_price) as avg_price_7d
    from losses l
    join stg_postings p
      on p.sku = l.sku and p.is_sold and p.delivered_date between l.date - interval 7 day and l.date - interval 1 day
    group by all
),
received as (
    select sku, sum(amount) as compensation_received
    from stg_accrual_lines where pnl_line = 'compensation'
    group by all
),
by_sku as (
    select l.sku, min(l.date) as first_loss_date, string_agg(distinct l.cluster, ', ') as clusters,
           sum(l.lost_qty) as lost_qty,
           sum(l.lost_qty * r.landed_cost_rub) as lost_cost_rub,
           sum(l.lost_qty * coalesce(p.avg_price_7d, 0)) as lost_value_at_sale_price,
           sum(l.lost_qty * coalesce(p.avg_price_7d, 0) * pr.economics_compensation_coef
               / (1 + pr.economics_vat_rate_in_card)) as expected_compensation_limit
    from losses l
    join ref_sku r on r.sku = l.sku
    left join price7 p on p.sku = l.sku and p.date = l.date
    cross join params pr
    group by all
)
select b.*, coalesce(rc.compensation_received, 0) as compensation_received,
       round(b.expected_compensation_limit - coalesce(rc.compensation_received, 0), 2) as claim_gap_rub,
       case when coalesce(rc.compensation_received, 0) = 0 then 'Возмещения нет — запросить расчёт, претензия'
            when b.expected_compensation_limit - rc.compensation_received > 1 then 'Возмещение меньше лимита — проверить расчёт'
            else 'Возмещено' end as status
from by_sku b
left join received rc on rc.sku = b.sku;

-- Приёмка поставок: отгружено vs принято.
create or replace table mart_supply_acceptance as
select s.supply_id, s.sku, r.offer_id, s.cluster, s.shipped_date::date as shipped_date,
       s.accepted_date::date as accepted_date, s.shipped_qty, s.accepted_qty,
       s.accepted_qty - s.shipped_qty                         as diff_qty,
       (s.accepted_qty - s.shipped_qty) * r.landed_cost_rub   as diff_cost_rub,
       case when s.accepted_qty < s.shipped_qty then 'Недостача при приёмке — акт и претензия'
            when s.accepted_qty > s.shipped_qty then 'Излишек — проверить пересорт'
            else 'OK' end                                     as status
from raw_supplies s
join ref_sku r on r.sku = s.sku;
