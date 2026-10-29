/* Host-owned CUA session: capture the rendered Reddit feed only. No navigation,
 * network, hidden state, mutations, caller selectors or caller scripts.
 * Per-article metadata is read before applying any returned-link cap.
 */
export async function captureRedditFeed(tab) {
  const coverage = { status: 'partial', rendered_only: true, can_advance_cutoff: false };
  let sourceURL = null;
  try {
    sourceURL = await tab.url();
    const parsed = new URL(sourceURL);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password ||
        !(parsed.hostname === 'reddit.com' || parsed.hostname.endsWith('.reddit.com'))) {
      return { status: 'needs_inspection', source_url: sourceURL, items: [], coverage,
        blockers: [{ code: 'reddit_url_unverified', message: 'The owned tab must show a credential-free HTTPS Reddit URL.' }] };
    }
    const captured = await tab.playwright.getByRole('article').evaluateAll(articles => {
      const isVisible = node => node.getClientRects().length && !node.closest('[hidden], [aria-hidden="true"]');
      const matched = articles.filter(isVisible);
      const leaves = matched.filter(node => !matched.some(other => other !== node && node.contains(other)));
      const declaredCounts = [...new Set(leaves.map(node => Number(node.getAttribute('aria-setsize')))
        .filter(value => Number.isInteger(value) && value > 0))];
      const items = leaves.slice(0, 100).map(node => {
        const body = String(node.innerText || '').trim();
        const anchors = Array.from(node.querySelectorAll('a[href]')).filter(isVisible).map(anchor => ({
          href: anchor.href || anchor.getAttribute('href'), text: String(anchor.innerText || '').trim()
        }));
        // Restrict to post paths: a comment permalink has another component
        // after the optional post slug and must never become the post identity.
        const postAnchors = anchors.filter(anchor => {
          try {
            const url = new URL(anchor.href);
            return url.protocol === 'https:' && !url.username && !url.password &&
              (url.hostname === 'reddit.com' || url.hostname.endsWith('.reddit.com')) &&
              /^\/r\/[^/]+\/comments\/[a-z0-9]+(?:\/[^/]+)?\/?$/i.test(url.pathname);
          } catch { return false; }
        });
        const post = postAnchors[0] || null;
        const author = anchors.find(anchor => {
          try {
            const url = new URL(anchor.href);
            return url.protocol === 'https:' && !url.username && !url.password &&
              (url.hostname === 'reddit.com' || url.hostname.endsWith('.reddit.com')) &&
              /^\/(?:user|u)\/[^/]+\/?$/i.test(url.pathname);
          } catch { return false; }
        });
        const heading = Array.from(node.querySelectorAll('h1,h2,h3,[role="heading"]')).find(isVisible);
        const time = Array.from(node.querySelectorAll('time')).find(isVisible);
        const buttonTexts = Array.from(node.querySelectorAll('button')).filter(isVisible)
          .map(button => String(button.innerText || button.getAttribute('aria-label') || '').trim());
        const collapsed = buttonTexts.some(text => /^(?:(?:\.{3}|…)\s*)?(?:show more|read more)(?:\b|$)/i.test(text));
        return { content_text: body.slice(0, 12000), title: String(heading?.innerText || node.getAttribute('aria-label') || post?.text || '').trim().slice(0, 2000) || null,
          rendered_body_length: body.length, body_cap_truncated: body.length > 12000,
          collapsed_body: collapsed, truncated: body.length > 12000 || collapsed,
          permalink: post?.href || null, author: author?.text || null, author_url: author?.href || null,
          published_raw: String(time?.innerText || '').trim() || null,
          published_datetime: time?.getAttribute('datetime') || null,
          position: node.getAttribute('aria-posinset') || null,
          anchors: anchors.slice(0, 40), anchors_truncated: anchors.length > 40 };
      });
      return { items, matched_visible_count: matched.length, visible_count: leaves.length,
        outer_article_count: matched.length - leaves.length, omitted_visible_count: Math.max(0, leaves.length - 100),
        declared_count: declaredCounts.length === 1 ? declaredCounts[0] : null };
    });
    const items = captured.items.map(item => {
      const permalink = item.permalink ? new URL(item.permalink) : null;
      if (permalink) { permalink.search = ''; permalink.hash = ''; }
      const match = permalink?.pathname.match(/^\/r\/([^/]+)\/comments\/([a-z0-9]+)/i);
      const exact = item.published_datetime && /^\d{4}-\d{2}-\d{2}T/.test(item.published_datetime) && Number.isFinite(Date.parse(item.published_datetime));
      return { ...item, source_url: sourceURL, permalink: permalink?.href || null,
        canonical_url: permalink?.href || null, native_id: match?.[2] || null,
        subreddit: match?.[1] || null, provisional_identity: !match,
        published_at: exact ? new Date(item.published_datetime).toISOString() : null,
        date_precision: exact ? 'exact' : item.published_raw ? 'rendered_label' : 'unknown' };
    });
    const blockers = [];
    if (!items.length) blockers.push({ code: 'no_rendered_articles', message: 'No visible leaf articles were captured; this does not establish an empty feed.' });
    if (items.some(item => !item.permalink)) blockers.push({ code: 'missing_permalink', message: 'Some articles lack a rendered post permalink; their identities remain provisional.' });
    if (items.some(item => item.truncated)) blockers.push({ code: 'body_incomplete', message: 'Some bodies are collapsed or exceed the 12000-character cap.' });
    if (captured.omitted_visible_count) blockers.push({ code: 'article_limit', message: 'The capture is bounded to the first 100 visible leaf articles.' });
    return { status: 'partial', source_url: sourceURL, items, declared_count: captured.declared_count,
      coverage: { ...coverage, matched_visible_count: captured.matched_visible_count, visible_count: captured.visible_count,
        captured_count: items.length, outer_article_count: captured.outer_article_count,
        omitted_visible_count: captured.omitted_visible_count }, blockers };
  } catch (error) {
    return { status: 'blocked', source_url: sourceURL, items: [], coverage,
      blockers: [{ code: 'reddit_capture_failed', message: String(error.message || error).slice(0, 500) }] };
  }
}
