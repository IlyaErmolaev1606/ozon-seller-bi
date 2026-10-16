-- Юнит-экономика SKU × месяц: «сколько на самом деле зарабатывает каждый артикул».
-- Знак: доходы > 0, расходы < 0 (как в начислениях Ozon); итоговые расходы показаны положительными.
create or replace table mart_sku_month as
with lines as (
    select month, sku,
        sum(amount) filter (where pnl_line = 'revenue')           as revenue,
        sum(amount) filter (where pnl_line = 'commission')        as commission,
        sum(amount) filter (where pnl_line = 'logistics')         as logistics,
        sum(amount) filter (where pnl_line = 'last_mile')         as last_mile,
        sum(amount) filter (where pnl_line = 'processing')        as processing,
        sum(amount) filter (where pnl_line = 'reverse_logistics') as reverse_logistics,
        sum(amount) filter (where pnl_line = 'acquiring')         as acquiring,
        sum(amount) filter (where pnl_line = 'storage')           as storage,
        sum(amount) filter (where pnl_line = 'reviews')           as reviews,
        sum(amount) filter (where pnl_line = 'points')            as points_offset,
        sum(amount) filter (where pnl_line = 'compensation')      as compensation
    from stg_accrual_lines
    where sku is not null
    group by all
),
units as (
    select strftime(delivered_date, '%Y-%m') as month, sku,
           count(*) filter (where is_sold)                          as units_sold,
           sum(ozon_discount_rub) filter (where is_sold)            as points_earned_gross
    from stg_postings where delivered_date is not null group by all
),
rets as (
    select strftime(outcome_date, '%Y-%m') as month, sku,
           count(*)                                                 as units_returned,
           count(*) filter (where return_reason = 'defect')         as units_defect,
           sum(ozon_discount_rub)                                   as points_reversed
    from stg_postings where is_returned group by all
),
-- Реклама: итог берём из финансов (это деньги), разбивку по SKU — из Performance API.
ads_fin as (
    select month, -sum(amount) as ads_total from stg_accrual_lines where pnl_line = 'ads' group by all
),
ads_perf as (
    select strftime(date::date, '%Y-%m') as month, sku, sum(spend) as spend
    from raw_performance_ads group by all
),
ads_alloc as (
    select p.month, p.sku, f.ads_total * p.spend / sum(p.spend) over (partition by p.month) as ads
    from ads_perf p join ads_fin f using (month)
),
-- Нераспределённые начисления кабинета (штрафы, неизвестные статьи) — по доле выручки.
overhead as (
    select month, -sum(amount) as overhead_total
    from stg_accrual_lines
    where sku is null and pnl_line not in ('ads')
    group by all
),
losses as (
    select strftime(first_loss_date, '%Y-%m') as month, sku, lost_cost_rub
    from mart_stock_losses
),
base as (
    select l.month, l.sku, r.offer_id, r.model_id, r.category, r.name, r.ozon_discounts_enabled,
           coalesce(u.units_sold, 0)                                   as units_sold,
           coalesce(rt.units_returned, 0)                              as units_returned,
           coalesce(rt.units_defect, 0)                                as units_defect,
           coalesce(l.revenue, 0)                                      as revenue,
           coalesce(l.commission, 0)                                   as commission,
           coalesce(l.logistics, 0)                                    as logistics,
           coalesce(l.last_mile, 0)                                    as last_mile,
           coalesce(l.processing, 0)                                   as processing,
           coalesce(l.reverse_logistics, 0)                            as reverse_logistics,
           coalesce(l.acquiring, 0)                                    as acquiring,
           coalesce(l.storage, 0)                                      as storage,
           coalesce(l.reviews, 0)                                      as reviews,
           coalesce(l.points_offset, 0)                                as points_offset,
           coalesce(l.compensation, 0)                                 as compensation,
           coalesce(u.points_earned_gross, 0) - coalesce(rt.points_reversed, 0) as points_earned,
           coalesce(a.ads, 0)                                          as ads,
           coalesce(ls.lost_cost_rub, 0)                               as loss_writeoff,
           r.landed_cost_rub, r.ff_cost_per_unit
    from lines l
    join ref_sku r on r.sku = l.sku
    left join units u      on u.month = l.month and u.sku = l.sku
    left join rets rt      on rt.month = l.month and rt.sku = l.sku
    left join ads_alloc a  on a.month = l.month and a.sku = l.sku
    left join losses ls    on ls.month = l.month and ls.sku = l.sku
),
calc as (
    select b.*,
        -(commission + logistics + last_mile + processing + reverse_logistics + acquiring + storage + reviews)
                                                                       as ozon_fees_gross,
        revenue + commission + logistics + last_mile + processing + reverse_logistics
            + acquiring + storage + reviews + points_offset + compensation as payout,
        (units_sold - units_returned) * landed_cost_rub                as cogs,
        units_defect * landed_cost_rub                                 as defect_writeoff,
        units_sold * ff_cost_per_unit                                  as ff_cost,
        coalesce(o.overhead_total, 0) * revenue
            / nullif(sum(revenue) over (partition by b.month), 0)      as overhead_alloc,
        revenue * p.tax_vat_rate_sales / (1 + p.tax_vat_rate_sales)    as vat,
        (revenue - revenue * p.tax_vat_rate_sales / (1 + p.tax_vat_rate_sales)) * p.tax_usn_rate as income_tax
    from base b
    cross join params p
    left join overhead o on o.month = b.month
)
select *,
    ozon_fees_gross - points_offset                                    as ozon_fees_net,
    payout - ads - overhead_alloc - cogs - defect_writeoff - ff_cost - loss_writeoff - vat - income_tax
                                                                       as contribution,
    (payout - ads - overhead_alloc - cogs - defect_writeoff - ff_cost - loss_writeoff - vat - income_tax)
        / nullif(revenue, 0)                                           as margin_pct,
    (ozon_fees_gross - points_offset) / nullif(revenue, 0)             as effective_take_rate,
    ozon_fees_gross / nullif(revenue, 0)                               as nominal_take_rate,
    ads / nullif(revenue, 0)                                           as drr,
    points_earned - points_offset                                      as points_unused,
    units_returned::double / nullif(units_sold, 0)                     as return_rate
from calc;

-- Итоги месяца по кабинету.
create or replace table mart_kpi_month as
select month,
    sum(revenue)                                      as revenue,
    sum(units_sold)                                   as units_sold,
    sum(payout)                                       as payout,
    sum(ozon_fees_gross)                              as ozon_fees_gross,
    sum(points_offset)                                as points_offset,
    sum(ozon_fees_net)                                as ozon_fees_net,
    sum(ads)                                          as ads,
    sum(cogs + defect_writeoff + loss_writeoff)       as cogs_total,
    sum(contribution)                                 as contribution,
    sum(contribution) / nullif(sum(revenue), 0)       as margin_pct,
    sum(ozon_fees_net) / nullif(sum(revenue), 0)      as effective_take_rate,
    sum(ozon_fees_gross) / nullif(sum(revenue), 0)    as nominal_take_rate,
    sum(ads) / nullif(sum(revenue), 0)                as drr,
    count(*) filter (where contribution < 0)          as loss_making_skus
from mart_sku_month
group by month
order by month;
