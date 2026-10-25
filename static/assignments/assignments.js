(() => {
    const panel = document.querySelector('[data-assignment-audience]');
    const source = document.getElementById('assignment-audience-data');
    if (!panel || !source) return;
    const catalog = JSON.parse(source.textContent);
    const filters = [...panel.querySelectorAll('[data-audience-filter]')];
    const search = panel.querySelector('[data-audience-search]');
    const sendAll = panel.querySelector('[data-audience-all]');
    const list = panel.querySelector('[data-audience-list]');
    const empty = panel.querySelector('[data-audience-empty]');
    const count = panel.querySelector('[data-audience-count]');
    const selectButton = panel.querySelector('[data-audience-select]');
    const rows = [...list.querySelectorAll('input[type="checkbox"]')].map(input => ({input, row: input.closest('div')}));
    const students = new Map(catalog.students.map(student => [student.id, student]));
    const subjectFields = new Map();
    catalog.subjects.forEach(subject => {
        if (!subjectFields.has(subject.title)) subjectFields.set(subject.title, new Set());
        subjectFields.get(subject.title).add(subject.field_id);
    });
    const digits = new Intl.NumberFormat('fa-IR', {useGrouping: false});
    const normalize = value => value.toLowerCase().replace(/ي/g, 'ی').replace(/ك/g, 'ک').replace(/[۰-۹٠-٩]/g, digit => String('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩'.indexOf(digit) % 10)).trim();
    const values = () => Object.fromEntries(filters.map(filter => [filter.dataset.audienceFilter, filter.value]));
    const matches = (student, selected, includeSearch = true) => {
        if (!student) return false;
        if (selected.province && student.province !== selected.province) return false;
        if (selected.grade && student.grade !== selected.grade) return false;
        if (selected.field_of_study && student.field !== selected.field_of_study) return false;
        if (selected.subject && !subjectFields.get(selected.subject)?.has(student.field_id)) return false;
        return !includeSearch || normalize(search.value).split(/\s+/).filter(Boolean).every(term => student.search.includes(term));
    };
    const refreshOptions = () => {
        const parents = {};
        filters.forEach(filter => {
            const candidates = catalog.students.filter(student => matches(student, parents, false));
            const name = filter.dataset.audienceFilter;
            let available;
            if (name === 'subject') {
                const fieldIds = new Set(candidates.map(student => student.field_id));
                available = new Set(catalog.subjects.filter(subject => fieldIds.has(subject.field_id)).map(subject => subject.title));
            } else {
                const key = name === 'field_of_study' ? 'field' : name;
                available = new Set(candidates.map(student => student[key]));
            }
            [...filter.options].forEach(option => {
                const unavailable = Boolean(option.value && option.value !== filter.value && !available.has(option.value));
                option.hidden = unavailable;
                option.disabled = unavailable;
            });
            parents[name] = filter.value;
        });
    };
    const refresh = () => {
        const selected = values();
        let total = 0;
        let checked = 0;
        rows.forEach(({input, row}) => {
            const student = students.get(input.value);
            const visible = Boolean(student && matches(student, selected));
            row.hidden = !visible;
            input.disabled = !visible || sendAll.checked;
            if (!visible) input.checked = false;
            if (visible) {
                total += 1;
                if (sendAll.checked) input.checked = true;
                if (input.checked) checked += 1;
            }
        });
        const selectedCount = sendAll.checked ? total : checked;
        count.textContent = `${digits.format(total)} دانش‌آموز مطابق · ${digits.format(selectedCount)} نفر برای ارسال`;
        empty.hidden = total !== 0;
        list.hidden = total === 0;
        list.classList.toggle('is-all-selected', sendAll.checked);
        selectButton.disabled = total === 0;
        panel.classList.toggle('has-all-recipients', sendAll.checked);
    };
    filters.forEach((filter, index) => filter.addEventListener('change', () => {
        filters.slice(index + 1).forEach(child => {child.value = '';});
        refreshOptions();
        refresh();
    }));
    search.addEventListener('input', refresh);
    sendAll.addEventListener('change', () => {
        if (!sendAll.checked) rows.forEach(({input}) => {input.checked = false;});
        refresh();
    });
    rows.forEach(({input}) => input.addEventListener('change', refresh));
    selectButton.addEventListener('click', () => {
        sendAll.checked = false;
        const selected = values();
        rows.forEach(({input}) => {input.checked = matches(students.get(input.value), selected);});
        refresh();
    });
    panel.querySelector('[data-audience-clear]').addEventListener('click', () => {
        sendAll.checked = false;
        rows.forEach(({input}) => {input.checked = false;});
        refresh();
    });
    panel.querySelector('[data-audience-reset]').addEventListener('click', () => {
        filters.forEach(filter => {filter.value = '';});
        search.value = '';
        refreshOptions();
        refresh();
    });
    refreshOptions();
    refresh();
})();
