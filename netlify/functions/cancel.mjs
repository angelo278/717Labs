// Private link from the booking email: frees a booked slot so it can be booked again.
// Opening the link only shows a confirm button, because some email systems open links automatically.
import { getStore } from "@netlify/blobs";
import { signSlot } from "../lib/booking.mjs";

const page = (body) => new Response(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>717Labs booking</title><body style="font-family:Arial,sans-serif;color:#172B4D;max-width:520px;margin:60px auto;padding:0 20px;line-height:1.6">${body}</body>`,
  { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });

export default async (req) => {
  const url = new URL(req.url);
  let slot = url.searchParams.get("slot") || "", key = url.searchParams.get("key") || "";
  if (req.method === "POST") { const f = await req.formData(); slot = String(f.get("slot") || ""); key = String(f.get("key") || ""); }
  if (!slot || key !== signSlot(slot)) return page("<h1>Link not valid</h1><p>This link is incomplete or has been changed.</p>");

  const store = getStore("bookings");
  const booking = await store.get(`slot:${slot}`, { type: "json" });
  const when = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/London", weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(slot));
  if (!booking) return page(`<h1>Already free</h1><p>${when} UK time is available on the website.</p>`);

  if (req.method === "POST") {
    await store.delete(`slot:${slot}`);
    return page(`<h1>Slot freed</h1><p>${when} UK time can now be booked again on the website.</p><p>Remember to let ${String(booking.name).replace(/</g, "&lt;")} know if you have not already.</p>`);
  }
  return page(`<h1>Free up this slot?</h1><p><strong>${when} UK time</strong><br>Booked by ${String(booking.name).replace(/</g, "&lt;")}</p>
<form method="post"><input type="hidden" name="slot" value="${slot}"><input type="hidden" name="key" value="${key}">
<button style="background:#2563EB;color:#fff;border:0;border-radius:999px;padding:12px 22px;font-size:15px;cursor:pointer">Yes, free this slot</button></form>`);
};

export const config = { path: "/api/cancel" };
