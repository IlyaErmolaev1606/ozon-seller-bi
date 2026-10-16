-- Начисления в разрезе строк с управленческой статьёй P&L.
-- Неизвестный type_id не теряется: pnl_line = 'unmapped' и попадает в контроль качества.
create or replace table stg_accrual_lines as
select
    a.accrual_id,
    a.date,
    strftime(a.date, '%Y-%m')                                   as month,
    a.category,
    a.unit_number,
    a.sku,
    a.type_id,
    a.amount,
    a.is_remainder,
    m.type_name,
    case
        when a.is_remainder then 'unattributed'
        when m.pnl_line is null then 'unmapped'
        else m.pnl_line
    end                                                         as pnl_line,
    coalesce(m.points_coverable, 0)                             as points_coverable
from raw_accrual_lines a
left join ref_accrual_type_map m on m.type_id = a.type_id;
