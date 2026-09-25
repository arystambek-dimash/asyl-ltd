"""Подпись отдела продаж в списках и отчётах."""

# Заказ без отдела (или с кодом удалённого отдела) показывается серым.
UNASSIGNED_NAME = "Нет отдела"
UNASSIGNED_COLOR = "#64748B"
# Код «Нет отдела» в фильтре ``?department=`` и в сводке отделов.
UNASSIGNED_CODE = "__unassigned"


def department_label(code: str, department) -> tuple[str, str]:
    """Имя и цвет отдела; без строки справочника — код (или «Нет отдела») серым."""
    if department is not None:
        return department.name, department.color
    return code or UNASSIGNED_NAME, UNASSIGNED_COLOR
