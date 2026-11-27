// Handles a consultation booking: checks the time, reserves it, and emails a confirmation
// with a calendar invite to the visitor, plus a copy to info@.
import { getStore } from "@netlify/blobs";
import { createHmac } from "node:crypto";

// Private link for freeing a booked slot (signed so nobody else can use it).
export const signSlot = (iso) => createHmac("sha256", process.env.BOOKING_SECRET || process.env.RESEND_API_KEY || "717labs").update(iso).digest("hex").slice(0, 32);
export const cancelLink = (iso) => `https://717labs.co.uk/api/cancel?slot=${encodeURIComponent(iso)}&key=${signSlot(iso)}`;

const INFO = "info@717labs.co.uk";
const FROM = process.env.BOOKING_FROM || "717Labs <bookings@717labs.co.uk>";
const TIMES = ["10:00", "11:30", "14:00", "15:30", "17:00"];          // UK times offered on the website
const MINUTES = 20;
const MEETING_LINK = process.env.MEETING_LINK || "";        // e.g. your Google Meet or Zoom personal link

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const validTZ = (tz) => { try { new Intl.DateTimeFormat("en-GB", { timeZone: tz }); return true; } catch { return false; } };
const when = (ms, tz) => new Intl.DateTimeFormat("en-GB", {
  timeZone: tz, weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit", hour12: false,
}).format(new Date(ms));
const ukHM = (ms) => new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/London", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(ms));
const ukDay = (ms) => new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/London", weekday: "short" }).format(new Date(ms));
const icsDate = (ms) => new Date(ms).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
const icsText = (s) => String(s).replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/[,;]/g, (c) => "\\" + c);

function invite({ uid, start, name, email, website }) {
  const desc = [
    `Free 20-minute consultation with 717Labs about ${website || "your business"}'s visibility in AI search.`,
    MEETING_LINK ? `Join: ${MEETING_LINK}` : "We will email the video link before the call.",
    `Need to change the time? Reply to the confirmation email or write to ${INFO}.`,
  ].join("\n\n");
  return [
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//717Labs//Bookings//EN", "METHOD:PUBLISH", "BEGIN:VEVENT",
    `UID:${uid}@717labs.co.uk`, `DTSTAMP:${icsDate(Date.now())}`,
    `DTSTART:${icsDate(start)}`, `DTEND:${icsDate(start + MINUTES * 60000)}`,
    `SUMMARY:${icsText(`717Labs consultation with ${name}`)}`,
    `DESCRIPTION:${icsText(desc)}`,
    `LOCATION:${icsText(MEETING_LINK || "Video call (link to follow)")}`,
    `ORGANIZER;CN=717Labs:mailto:${INFO}`,
    `ATTENDEE;CN=${icsText(name)}:mailto:${email}`,
    "BEGIN:VALARM", "TRIGGER:-PT15M", "ACTION:DISPLAY", "DESCRIPTION:717Labs consultation", "END:VALARM",
    "END:VEVENT", "END:VCALENDAR",
  ].join("\r\n");
}

async function send({ to, subject, html, ics }) {
  const body = { from: FROM, to: [to], subject, html, reply_to: INFO };
  if (ics) body.attachments = [{ filename: "717labs-consultation.ics", content: Buffer.from(ics).toString("base64") }];
  const r = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${process.env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) console.error("Resend failed", r.status, await r.text());
}

const wrap = (inner) => `<div style="font-family:Arial,Helvetica,sans-serif;color:#172B4D;font-size:15px;line-height:1.6;max-width:560px">${inner}
<p style="margin-top:28px">Kind regards,<br><strong>717Labs</strong><br><a href="https://717labs.co.uk" style="color:#2563EB">717labs.co.uk</a></p></div>`;

export async function handleBooking(payload) {
  const d = payload.data || {};
  if (d["company-url"]) return "honeypot";
  const name = String(d.name || "").trim().slice(0, 120) || "there";
  const email = String(d.email || "").trim().slice(0, 200);
  const website = String(d.website || "").trim().slice(0, 300);
  const tz = validTZ(d.tz) ? d.tz : "Europe/London";
  const start = Date.parse(String(d.slot_utc || ""));
  if (!email.includes("@")) return "invalid email";

  // Only accept the weekday times the website offers, between now and three weeks ahead.
  const ok = Number.isFinite(start) && start > Date.now() + 30 * 60000 && start < Date.now() + 21 * 864e5
    && TIMES.includes(ukHM(start)) && !["Sat", "Sun"].includes(ukDay(start));
  const first = esc(name.split(/\s+/)[0]);

  if (!ok) {
    await send({ to: INFO, subject: `Booking needs a manual reply: ${name}`,
      html: wrap(`<p>A consultation request came in without a valid time.</p><p>Name: ${esc(name)}<br>Email: ${esc(email)}<br>Website: ${esc(website)}<br>Requested: ${esc(d.slot || d["slot-text"] || "not given")}</p><p>Please reply to them with a time.</p>`) });
    await send({ to: email, subject: "Your 717Labs consultation request",
      html: wrap(`<p>Hi ${first},</p><p>Thank you for your request. We could not read the time you chose, so we will reply shortly to arrange a time that suits you.</p>`) });
    return "manual";
  }

  const iso = new Date(start).toISOString();
  const store = getStore("bookings");
  const key = `slot:${iso}`;
  const existing = await store.get(key);
  const localWhen = when(start, tz), ukWhen = when(start, "Europe/London");
  const timeLine = tz === "Europe/London" ? `${ukWhen} (UK time)` : `${localWhen} your time<br><span style="color:#3E4F6E">${ukWhen} UK time</span>`;

  if (existing) {
    await send({ to: email, subject: "Your 717Labs consultation: please choose another time",
      html: wrap(`<p>Hi ${first},</p><p>Thank you for booking. Unfortunately, the time you chose (${esc(localWhen)}) was taken moments before your request arrived.</p><p>Please reply to this email with two or three times that suit you, or <a href="https://717labs.co.uk/#chat" style="color:#2563EB">choose another time on our website</a>.</p>`) });
    await send({ to: INFO, subject: `Double booking avoided: ${name}`,
      html: wrap(`<p>${esc(name)} (${esc(email)}) tried to book ${esc(ukWhen)} UK time, which was already taken. They have been asked to pick another time.</p>`) });
    return "clash";
  }

  await store.setJSON(key, { name, email, website, tz, id: payload.id || "", bookedAt: new Date().toISOString() });
  const ics = invite({ uid: payload.id || iso, start, name, email, website });
  const join = MEETING_LINK
    ? `<p><strong>Join the call:</strong> <a href="${esc(MEETING_LINK)}" style="color:#2563EB">${esc(MEETING_LINK)}</a></p>`
    : `<p>We will email you the video link before the call.</p>`;

  await send({ to: email, subject: `Confirmed: your 717Labs consultation, ${localWhen}`, ics,
    html: wrap(`<p>Hi ${first},</p><p>Your free consultation is confirmed.</p>
<p style="background:#F3F6FB;border-radius:10px;padding:14px 16px"><strong>${timeLine}</strong><br>20 minutes, video or phone call</p>${join}
<p>We will talk through how your business currently appears in AI answers and what would make the biggest difference. If you have not already done so, you can also <a href="https://717labs.co.uk/#report" style="color:#2563EB">request your free AI visibility report</a> beforehand.</p>
<p>A calendar invite is attached. If you need to change the time, simply reply to this email.</p>`) });

  await send({ to: INFO, subject: `New consultation: ${name}, ${ukWhen} UK`, ics,
    html: wrap(`<p><strong>New consultation booked.</strong></p><p>${esc(ukWhen)} UK time<br>(their time: ${esc(localWhen)}, ${esc(tz)})</p>
<p>Name: ${esc(name)}<br>Email: <a href="mailto:${esc(email)}">${esc(email)}</a><br>Website: ${esc(website || "not given")}</p>
<p>The calendar invite is attached. Tip: request a free report for their website before the call.</p>
<p style="color:#3E4F6E;font-size:13px">If this call is cancelled or moved, <a href="${cancelLink(iso)}">free up this time slot</a> so someone else can book it.</p>`) });
  return "booked";
}
