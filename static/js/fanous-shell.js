document.addEventListener('DOMContentLoaded', () => {
  const links = [...document.querySelectorAll('.sidebar-link[href]')];
  const current = window.location.pathname.replace(/\/+$/, '/') || '/';
  const candidates = links.map((link, index) => {
    const path = (link.dataset.navPrefix || new URL(link.href, window.location.origin).pathname).replace(/\/+$/, '/') || '/';
    const matches = path === '/dashboard/' ? current === path : path !== '/' && current.startsWith(path);
    return { link, path, index, matches, active: link.classList.contains('active') };
  }).filter(item => item.matches).sort((a, b) => b.path.length - a.path.length || Number(b.active) - Number(a.active) || a.index - b.index);
  links.forEach(link => { link.classList.remove('active', 'sidebar-link-active'); link.removeAttribute('aria-current'); });
  if (candidates[0]) {
    candidates[0].link.classList.add('active');
    candidates[0].link.setAttribute('aria-current', 'page');
  }

  const button = document.getElementById('notification-toggle');
  const panel = document.getElementById('notification-panel');
  const initialFeed = document.getElementById('fanous-event-feed');
  if (!button || !panel || !initialFeed) return;
  let feed = JSON.parse(initialFeed.textContent);
  if (!feed || !Array.isArray(feed.events) || !Array.isArray(feed.notifications)) return;
  let clockOffset = Date.parse(feed.now) - Date.now();
  let marking = false;
  let refreshing = false;
  const numberFormat = new Intl.NumberFormat('fa-IR', {minimumIntegerDigits: 2, useGrouping: false});
  const dateFormat = new Intl.DateTimeFormat('fa-IR', {month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit'});
  const notified = new Set();
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const eventLabel = event => event.kind === 'quiz' ? 'آزمون' : 'کلاس';
  const eventKey = (event, live) => `${event.id}:${event.starts_at}:${live ? 'live' : 'upcoming'}`;
  const now = () => Date.now() + clockOffset;
  const setOpen = open => {
    panel.classList.toggle('hidden', !open);
    button.classList.toggle('is-open', open);
    button.setAttribute('aria-expanded', String(open));
    if (open) {
      document.getElementById('profile-menu')?.classList.add('hidden');
      document.getElementById('profile-toggle')?.setAttribute('aria-expanded', 'false');
    }
  };
  const updateBadge = () => {
    const count = feed.notifications.filter(item => item.unread).length;
    let badge = document.getElementById('notification-count');
    if (count && !badge) {
      badge = element('span', 'fanous-notification-count');
      badge.id = 'notification-count';
      button.append(badge);
    }
    if (count) badge.textContent = new Intl.NumberFormat('fa-IR').format(count);
    else badge?.remove();
    const summary = document.querySelector('[data-notification-summary]');
    if (summary) summary.textContent = `${new Intl.NumberFormat('fa-IR').format(count)} تازه`;
  };
  const renderNotifications = () => {
    const list = document.getElementById('notification-items');
    if (!list) return;
    list.replaceChildren();
    feed.notifications.forEach(item => {
      const link = element('a', 'fanous-notification-item');
      link.href = item.url;
      const symbol = element('span', `notification-symbol ${item.kind}`);
      const icons = {blog: 'fa-pen-nib', news: 'fa-newspaper', classroom: 'fa-chalkboard-user', quiz: 'fa-clipboard-question'};
      symbol.append(element('i', `fa-solid ${icons[item.kind] || 'fa-bell'}`));
      const body = element('span', 'min-w-0');
      body.append(element('strong', 'block text-xs text-slate-700', item.title));
      body.append(element('small', 'mt-1 block text-[10px] text-slate-400', item.description || (item.kind === 'blog' ? 'مطلب جدید مجله' : 'خبر تازه')));
      link.append(symbol, body);
      list.append(link);
    });
    if (!feed.notifications.length) list.append(element('p', 'px-5 py-9 text-center text-xs text-slate-400', 'فعلاً اعلان تازه‌ای نیست.'));
    updateBadge();
  };
  const csrfToken = () => document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || button?.dataset.csrf || '';
  const markRead = async () => {
    if (marking || !feed.notifications.some(item => item.unread)) return;
    marking = true;
    const displayed = [...feed.notifications];
    try {
      const response = await fetch(button.dataset.readUrl, {
        method: 'POST',
        headers: {'X-CSRFToken': csrfToken(), 'Content-Type': 'application/json'},
        body: JSON.stringify({event_keys: displayed.filter(item => item.key).map(item => item.key), seen_at: feed.now}),
        credentials: 'same-origin'
      });
      if (!response.ok || !(await response.json()).ok) throw new Error('mark-read failed');
      displayed.forEach(item => { item.unread = false; });
      updateBadge();
    } catch (_) {
      button.setAttribute('title', 'ثبت خواندن اعلان‌ها انجام نشد؛ دوباره تلاش کنید.');
    } finally { marking = false; }
  };
  button.addEventListener('click', event => {
    event.stopPropagation();
    setOpen(panel.classList.contains('hidden'));
    if (!panel.classList.contains('hidden')) markRead();
  });
  document.addEventListener('click', event => {
    if (!panel.contains(event.target) && !button.contains(event.target)) setOpen(false);
    if (event.target.closest('[data-event-link][aria-disabled="true"]')) event.preventDefault();
  });
  document.addEventListener('fanous:close-notifications', () => setOpen(false));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.classList.contains('hidden')) {
      setOpen(false);
      button.focus();
    }
  });

  const renderSchedule = () => {
    document.querySelectorAll('[data-schedule-list]').forEach(list => {
      const events = feed.events.filter(event => event.kind === list.dataset.scheduleList && Date.parse(event.ends_at) > now());
      list.replaceChildren();
      events.forEach(event => {
        const card = element('article', 'schedule-event');
        card.dataset.eventId = event.id;
        const top = element('div', 'schedule-event-top');
        const status = element('span', 'schedule-status');
        status.dataset.eventStatus = '';
        const heading = element('div', 'schedule-event-heading');
        const symbol = element('span', 'schedule-event-symbol');
        const icon = element('i', `fa-solid ${event.kind === 'quiz' ? 'fa-clipboard-question' : 'fa-video'}`);
        icon.setAttribute('aria-hidden', 'true');
        symbol.append(icon);
        heading.append(symbol, element('h3', '', event.title));
        top.append(heading, status);
        const date = element('time', 'schedule-date', dateFormat.format(new Date(event.starts_at)));
        date.dateTime = event.starts_at;
        const bottom = element('div', 'schedule-event-bottom');
        const dateRow = element('div', 'schedule-date-row');
        const calendarIcon = element('i', 'fa-regular fa-calendar');
        calendarIcon.setAttribute('aria-hidden', 'true');
        dateRow.append(calendarIcon, date);
        const countdown = element('div', 'schedule-timer-block');
        const label = element('small', '');
        label.dataset.timerLabel = '';
        const timer = element('strong', 'schedule-countdown');
        timer.dataset.eventTimer = '';
        timer.dir = 'ltr';
        const units = element('span', 'schedule-timer-units');
        units.dir = 'ltr';
        ['ساعت', 'دقیقه', 'ثانیه'].forEach(unit => units.append(element('span', '', unit)));
        countdown.append(label, timer, units);
        const link = element('a', 'schedule-enter', event.kind === 'quiz' ? 'مشاهده آزمون' : 'ورود به کلاس');
        link.dataset.eventLink = '';
        link.href = event.url;
        link.append(element('i', 'fa-solid fa-arrow-left'));
        bottom.append(countdown, link);
        card.append(top, dateRow, bottom);
        list.append(card);
      });
      const empty = document.querySelector(`[data-schedule-empty="${list.dataset.scheduleList}"]`);
      if (empty) empty.hidden = events.length > 0;
      const count = document.querySelector(`[data-schedule-count="${list.dataset.scheduleList}"]`);
      if (count) count.textContent = new Intl.NumberFormat('fa-IR').format(events.length);
    });
  };
  const toast = event => {
    const key = `fanous-start:${feed.user_id}:${eventKey(event, true)}`;
    if (notified.has(key)) return;
    notified.add(key);
    try {
      if (sessionStorage.getItem(key)) return;
      sessionStorage.setItem(key, '1');
    } catch (_) {}
    const container = document.getElementById('fanous-event-toasts');
    if (!container) return;
    const notice = element('div', `fanous-event-toast ${event.kind}`);
    const link = element('a', '', `${eventLabel(event)} شروع شد: ${event.title}`);
    link.href = event.url;
    const close = element('button', '', '×');
    close.type = 'button';
    close.setAttribute('aria-label', 'بستن اعلان');
    close.addEventListener('click', () => notice.remove());
    notice.append(link, close);
    container.append(notice);
    while (container.children.length > 3) container.firstElementChild.remove();
    window.setTimeout(() => notice.remove(), 15000);
  };
  const tick = () => {
    const currentTime = now();
    let changed = false;
    feed.events.forEach(event => {
      const start = Date.parse(event.starts_at);
      const end = Date.parse(event.ends_at);
      const live = currentTime >= start && currentTime < end;
      if (live) toast(event);
      if (currentTime >= end) {
        const previousCount = feed.notifications.length;
        feed.notifications = feed.notifications.filter(item => !item.key?.startsWith(`${event.id}:`));
        changed = changed || previousCount !== feed.notifications.length;
      } else if (live && !event.live) {
        event.live = true;
        feed.notifications = feed.notifications.filter(item => !item.key?.startsWith(`${event.id}:`));
        feed.notifications.unshift({key: eventKey(event, true), kind: event.kind, title: `${eventLabel(event)} شروع شد: ${event.title}`, description: 'اکنون می‌توانی وارد شوی', url: event.url, unread: true});
        changed = true;
      }
      const card = document.querySelector(`[data-event-id="${event.id}"]`);
      if (!card) return;
      const ended = currentTime >= end;
      card.classList.toggle('is-live', live);
      card.classList.toggle('is-ended', ended);
      const status = card.querySelector('[data-event-status]');
      status.textContent = ended ? `${eventLabel(event)} پایان یافت` : live ? `${eventLabel(event)} شروع شد` : 'به‌زودی';
      status.classList.toggle('is-live', live);
      card.querySelector('[data-timer-label]').textContent = ended ? `${eventLabel(event)} پایان یافته است` : `زمان باقی‌مانده تا ${live ? 'پایان' : 'شروع'} ${eventLabel(event)}`;
      const seconds = ended ? 0 : Math.max(0, Math.ceil(((live ? end : start) - currentTime) / 1000));
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor(seconds % 3600 / 60);
      const timer = card.querySelector('[data-event-timer]');
      timer.textContent = [hours, minutes, seconds % 60].map(value => numberFormat.format(value)).join(':');
      timer.setAttribute('aria-label', `${hours} ساعت و ${minutes} دقیقه و ${seconds % 60} ثانیه، ${card.querySelector('[data-timer-label]').textContent}`);
      const link = card.querySelector('[data-event-link]');
      link.setAttribute('aria-disabled', String(!live));
      link.tabIndex = live ? 0 : -1;
    });
    if (changed) renderNotifications();
  };
  const refresh = async () => {
    if (refreshing || document.hidden) return;
    refreshing = true;
    try {
      const response = await fetch(button.dataset.feedUrl, {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) throw new Error('feed failed');
      const nextFeed = await response.json();
      feed = nextFeed;
      clockOffset = Date.parse(feed.now) - Date.now();
      renderSchedule();
      renderNotifications();
      tick();
    } catch (_) {
      button.setAttribute('title', 'به‌روزرسانی اعلان‌ها موقتاً در دسترس نیست.');
    } finally { refreshing = false; }
  };
  renderSchedule();
  renderNotifications();
  tick();
  window.setInterval(tick, 1000);
  window.setInterval(refresh, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { tick(); refresh(); } });
});
