(function () {
    const fields = JSON.parse(document.getElementById('course-curriculum-fields').textContent);
    const books = JSON.parse(document.getElementById('course-curriculum-books').textContent);
    const selected = function (name) {
        return new Set(Array.from(document.querySelectorAll(`input[name="${name}"]:checked`), input => Number(input.value)));
    };
    const filterOptions = function (name, items, accepts) {
        document.querySelectorAll(`input[name="${name}"]`).forEach(function (input) {
            const item = items.find(record => record.id === Number(input.value));
            const visible = item && accepts(item);
            input.closest('li').hidden = !visible;
            input.disabled = !visible;
            if (!visible) input.checked = false;
        });
    };
    const refresh = function () {
        const grades = selected('allowed_grades');
        const source = document.getElementById('curriculum-selection-data');
        const generalGrades = new Set(source ? JSON.parse(source.textContent).grades.filter(grade => grade.general).map(grade => grade.id) : []);
        filterOptions('allowed_fields', fields, field => !generalGrades.has(field.grade_id) && (!grades.size || grades.has(field.grade_id)));
        const activeFields = selected('allowed_fields');
        filterOptions('subjects', books, book => (!grades.size || grades.has(book.field__grade_id)) && (generalGrades.has(book.field__grade_id) || !activeFields.size || activeFields.has(book.field_id)));
    };
    document.querySelectorAll('input[name="allowed_grades"], input[name="allowed_fields"]').forEach(function (input) {
        input.addEventListener('change', refresh);
    });
    refresh();
})();
