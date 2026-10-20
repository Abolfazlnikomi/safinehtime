document.addEventListener('DOMContentLoaded', function () {
    const source = document.getElementById('curriculum-selection-data');
    if (!source) return;
    const data = JSON.parse(source.textContent);
    const grades = new Map(data.grades.map(grade => [String(grade.id), grade]));
    const fields = new Map(data.fields.map(field => [String(field.id), field]));
    document.querySelectorAll('select[name="grade"]').forEach(function (gradeSelect) {
        const fieldSelect = gradeSelect.form?.querySelector('select[name="field"]');
        if (!fieldSelect) return;
        const refresh = function () {
            const grade = grades.get(gradeSelect.value);
            const general = Boolean(grade?.general);
            Array.from(fieldSelect.options).forEach(function (option) {
                const field = fields.get(option.value);
                option.hidden = Boolean(field && (field.title === 'عمومی' || (grade && String(field.grade_id) !== gradeSelect.value)));
            });
            if (general) {
                const neutral = data.fields.find(field => String(field.grade_id) === gradeSelect.value && field.title === 'عمومی');
                fieldSelect.value = neutral && fieldSelect.querySelector(`option[value="${neutral.id}"]`) ? String(neutral.id) : '';
                fieldSelect.disabled = true;
                fieldSelect.setAttribute('aria-label', 'پایه‌های پنجم تا نهم رشته ندارند');
            } else {
                fieldSelect.disabled = gradeSelect.disabled;
                fieldSelect.removeAttribute('aria-label');
                if (fieldSelect.selectedOptions[0]?.hidden) fieldSelect.value = '';
            }
            fieldSelect.dispatchEvent(new Event('change', {bubbles: true}));
        };
        gradeSelect.addEventListener('change', refresh);
        refresh();
    });
});
