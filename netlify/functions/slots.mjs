// Tells the booking form which consultation times are already taken, so nobody can double-book.
import { getStore } from "@netlify/blobs";

export default async () => {
  const store = getStore("bookings");
  const now = Date.now();
  const { blobs } = await store.list({ prefix: "slot:" });
  const taken = blobs
    .map((b) => b.key.slice(5))
    .filter((iso) => Date.parse(iso) > now);
  return Response.json({ taken }, { headers: { "Cache-Control": "no-store" } });
};

export const config = { path: "/api/slots" };
