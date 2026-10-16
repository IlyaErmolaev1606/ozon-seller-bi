-- Отправления: статус, даты, цены. «Продано» = выкуплено (delivered/returned) по дате доставки.
create or replace table stg_postings as
select
    posting_number,
    sku,
    offer_id,
    cluster_from,
    cluster_to,
    created_date::date                                          as created_date,
    status,
    delivered_date::date                                        as delivered_date,
    outcome_date::date                                          as outcome_date,
    seller_price::double                                        as seller_price,
    buyer_price::double                                         as buyer_price,
    seller_price::double - buyer_price::double                  as ozon_discount_rub,
    is_local::int = 1                                           as is_local,
    nullif(return_reason, '')                                   as return_reason,
    status in ('delivered', 'returned')                         as is_sold,
    status = 'returned'                                         as is_returned
from raw_postings;

-- Ежедневные движения по складам Ozon: отгрузки, приёмка, возвраты в сток.
create or replace table stg_stock_moves as
with outs as (
    select sku, cluster_from as cluster, created_date as date, count(*) as qty_out
    from stg_postings where status <> 'cancelled'
    group by all
),
accepted as (
    select sku, cluster, accepted_date::date as date, sum(accepted_qty) as qty_accepted
    from raw_supplies group by all
),
restocked as (
    select sku, cluster, restock_date::date as date, sum(qty) as qty_restocked
    from raw_returns where restock_date is not null group by all
)
select s.date::date as date, s.sku, s.cluster, s.qty as actual_qty,
       coalesce(o.qty_out, 0)       as qty_out,
       coalesce(a.qty_accepted, 0)  as qty_accepted,
       coalesce(r.qty_restocked, 0) as qty_restocked
from raw_stocks_ozon s
left join outs o      on o.sku = s.sku and o.cluster = s.cluster and o.date = s.date
left join accepted a  on a.sku = s.sku and a.cluster = s.cluster and a.date = s.date
left join restocked r on r.sku = s.sku and r.cluster = s.cluster and r.date = s.date;
