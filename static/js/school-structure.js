document.getElementById('structure-upload')?.addEventListener('submit', function () {
    const button = document.getElementById('structure-submit');
    button.disabled = true;
    button.textContent = 'در حال بررسی فایل‌ها…';
});
window.addEventListener('pageshow', function () {
    const button = document.getElementById('structure-submit');
    if (button) {
        button.disabled = false;
        button.textContent = 'بررسی و ثبت ساختار';
    }
});
const consultantSelect = document.getElementById('id_consultant');
if (consultantSelect) {
    const assignments = JSON.parse(document.getElementById('structure-scope-assignments').textContent);
    consultantSelect.addEventListener('change', function () {
        const assignedGrades = new Set(assignments[consultantSelect.value] || []);
        Array.from(document.getElementById('id_grades').options).forEach(function (option) {
            option.selected = assignedGrades.has(Number(option.value));
        });
    });
}
