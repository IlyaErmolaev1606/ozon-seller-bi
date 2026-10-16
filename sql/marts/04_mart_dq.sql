-- Контроль качества данных. Мониторинг «шаг прошёл» ничего не значит:
-- проверяем свежесть каждого источника и полноту, а не факт запуска.
create or replace table mart_dq as
with p as (select report_as_of::date as as_of from params)
select 'Свежесть: начисления' as check_name,
       (select max(date) from stg_accrual_lines)::varchar as detail,
       case when (select max(date) from stg_accrual_lines) >= (select as_of from p) - interval 3 day
            then 'OK' else 'Тревога' end as status,
       null::double as value_rub
union all
select 'Свежесть: Performance API',
       (select max(date::date) from raw_performance_ads)::varchar,
       case when (select max(date::date) from raw_performance_ads) >= (select as_of from p) - interval 1 day
            then 'OK' else 'Тревога' end, null
union all
select 'Свежесть: остатки Ozon',
       (select max(date::date) from raw_stocks_ozon)::varchar,
       case when (select max(date::date) from raw_stocks_ozon) >= (select as_of from p) - interval 1 day
            then 'OK' else 'Тревога' end, null
union all
select 'Новые типы начислений без маппинга',
       coalesce((select string_agg(distinct type_id::varchar, ', ') from stg_accrual_lines where pnl_line = 'unmapped'), '—'),
       case when exists (select 1 from stg_accrual_lines where pnl_line = 'unmapped') then 'Тревога' else 'OK' end,
       (select round(sum(amount), 2) from stg_accrual_lines where pnl_line = 'unmapped')
union all
select 'Начисления без детализации (только total_amount)',
       (select count(distinct accrual_id) from stg_accrual_lines where pnl_line = 'unattributed')::varchar || ' шт.',
       case when exists (select 1 from stg_accrual_lines where pnl_line = 'unattributed') then 'Проверить' else 'OK' end,
       (select round(sum(amount), 2) from stg_accrual_lines where pnl_line = 'unattributed')
union all
select 'Выкупленные отправления без начисления выручки',
       (select count(*) from stg_postings p
         where p.is_sold and not exists (select 1 from stg_accrual_lines a
                                          where a.unit_number = p.posting_number and a.type_id = 1))::varchar || ' шт.',
       case when (select count(*) from stg_postings p
                   where p.is_sold and not exists (select 1 from stg_accrual_lines a
                                                    where a.unit_number = p.posting_number and a.type_id = 1)) = 0
            then 'OK' else 'Тревога' end, null
union all
select 'Дни без данных Performance API при списаниях в финансах',
       coalesce((select string_agg(days_missing_in_performance, '; ') from mart_recon_ads
                  where days_missing_in_performance is not null), '—'),
       case when exists (select 1 from mart_recon_ads where days_missing_in_performance is not null)
            then 'Тревога' else 'OK' end, null;
