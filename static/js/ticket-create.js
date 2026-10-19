(() => {
    const form = document.getElementById('ticket-create-form');
    if (!form) return;

    const subjectSection = document.getElementById('ticket-subject');
    const subjectSelect = subjectSection?.querySelector('select');
    const topicInputs = form.querySelectorAll('input[type="radio"][name="ticket_type"]');
    if (!subjectSection || !subjectSelect) return;

    const updateSubject = () => {
        const selectedTopic = form.querySelector('input[name="ticket_type"]:checked');
        const isLesson = selectedTopic?.value === 'LESSON';
        subjectSection.hidden = !isLesson;
        subjectSelect.disabled = !isLesson;
        subjectSelect.required = isLesson;
        topicInputs.forEach((input) => {
            input.setAttribute('aria-controls', subjectSection.id);
            input.setAttribute('aria-expanded', String(input.checked && isLesson));
        });
    };

    topicInputs.forEach((input) => input.addEventListener('change', updateSubject));
    window.addEventListener('pageshow', updateSubject);
    form.addEventListener('reset', () => window.setTimeout(updateSubject, 0));
    updateSubject();
})();
