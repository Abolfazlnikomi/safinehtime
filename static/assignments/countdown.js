(() => {
    const counters = [...document.querySelectorAll('[data-assignment-deadline]')];
    if (!counters.length) return;
    const digits = new Intl.NumberFormat('fa-IR', {useGrouping: false});
    const update = () => {
        if (document.hidden) return;
        const now = Date.now();
        counters.forEach(counter => {
            const deadline = Date.parse(counter.dataset.assignmentDeadline);
            if (!Number.isFinite(deadline)) return;
            const seconds = Math.max(0, Math.floor((deadline - now) / 1000));
            const days = Math.floor(seconds / 86400);
            const hours = Math.floor((seconds % 86400) / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            const parts = [];
            if (days) parts.push(`${digits.format(days)} روز`);
            if (hours) parts.push(`${digits.format(hours)} ساعت`);
            if (!days && minutes) parts.push(`${digits.format(minutes)} دقیقه`);
            const label = seconds === 0 ? 'مهلت ارسال تمام شد' : parts.slice(0, 2).join(' و ') || 'کمتر از یک دقیقه';
            if (counter.textContent !== label) counter.textContent = label;
            counter.classList.toggle('is-expired', seconds === 0);
            counter.closest('.active-homework-card')?.classList.toggle('is-urgent', seconds > 0 && seconds <= 86400);
        });
    };
    update();
    window.setInterval(update, 1000);
    document.addEventListener('visibilitychange', update);
})();
