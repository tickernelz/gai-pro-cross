() => {
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const shown = (el) => {
    if (!el || !el.getClientRects().length) return false;
    const style = window.getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  const cards = Array.from(document.querySelectorAll('*')).filter((el) => {
    const text = clean(el.innerText);
    return /^Google AI Pro\b/i.test(text) && text.length < 1600 && el.querySelector('button');
  });
  cards.sort((a, b) => clean(a.innerText).length - clean(b.innerText).length);
  const card = cards.find(shown) || cards[0];
  if (!card) return null;
  const buttons = Array.from(card.querySelectorAll('button, a[role=button]')).filter((el) => {
    const label = clean(el.getAttribute('aria-label'));
    return shown(el) && clean(el.innerText) && !/informasi|info|learn more/i.test(label);
  });
  const cta = buttons.find((el) => /^(mulai|dapatkan|berlangganan|coba|start|get started|try|subscribe|upgrade)\b/i.test(clean(el.innerText)));
  if (cta) return cta;
  const size = /^\d+\s*(GB|TB)$/i;
  const rest = buttons.filter((el) => !size.test(clean(el.innerText)));
  return rest.length ? rest[0] : null;
}
