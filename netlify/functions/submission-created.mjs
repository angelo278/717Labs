// Runs automatically every time Netlify accepts a form submission (spam is already filtered out).
// It checks the request against simple limits, then asks GitHub to build and email the report.
import { getStore } from "@netlify/blobs";

const PER_SITE_DAYS = 30;                                   // one report per website per 30 days
const DAILY_CAP = Number(process.env.REPORT_DAILY_CAP || 40); // protects against floods and runaway costs

const domainOf = (url) => {
  try {
    const u = new URL(/^https?:\/\//i.test(url) ? url : `https://${url}`);
    return u.hostname.replace(/^www\./, "").toLowerCase();
  } catch { return ""; }
};

export default async (req) => {
  const { payload } = await req.json();
  if (!payload || payload.form_name !== "free-report") return new Response("ignored");

  const d = payload.data || {};
  if (d["company-url"]) return new Response("honeypot");      // bots fill the hidden field
  const email = String(d.email || "").trim();
  const domain = domainOf(String(d.website || "").trim());
  if (!email.includes("@") || !domain.includes(".")) return new Response("invalid");

  const store = getStore("report-limits");
  const now = Date.now();
  const today = new Date().toISOString().slice(0, 10);

  const last = await store.get(`site:${domain}`);
  const tooSoon = last && now - Number(last) < PER_SITE_DAYS * 864e5;
  const count = Number((await store.get(`day:${today}`)) || 0);
  const overCap = count >= DAILY_CAP;

  const res = await fetch(`https://api.github.com/repos/${process.env.GH_REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.GH_DISPATCH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
      "User-Agent": "717labs-report",
    },
    body: JSON.stringify({
      event_type: "report-request",
      client_payload: {
        id: payload.id || String(now),
        name: String(d.name || "").slice(0, 120),
        email: email.slice(0, 200),
        website: String(d.website || "").slice(0, 300),
        searches: String(d.searches || "").slice(0, 600),
        // When a limit is hit the robot sends you an alert instead of running a full report.
        mode: tooSoon ? "repeat" : overCap ? "over-cap" : "report",
      },
    }),
  });

  if (!res.ok) {
    console.error("GitHub dispatch failed", res.status, await res.text());
    return new Response("dispatch failed", { status: 500 });
  }
  if (!tooSoon && !overCap) {
    await store.set(`site:${domain}`, String(now));
    await store.set(`day:${today}`, String(count + 1));
  }
  return new Response("ok");
};
