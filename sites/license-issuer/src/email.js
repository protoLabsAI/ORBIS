// License-key delivery email via Resend (raw fetch). No-op (logs + returns
// false) when RESEND_API_KEY is unset, so the success page can still deliver
// the key without email configured.

export async function sendLicenseEmail(env, { to, token }) {
  if (!env.RESEND_API_KEY) {
    console.log("[email] RESEND_API_KEY unset — skipping email, key shown on success page only");
    return false;
  }
  const html = `
    <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:520px;margin:0 auto;color:#18181b">
      <h2 style="color:#6366f1">Your ORBIS customization unlock</h2>
      <p>Thanks for supporting ORBIS. Here is your license key — paste it into
         ORBIS under <strong>Settings → Unlock customization</strong>:</p>
      <pre style="background:#f4f4f5;border:1px solid #e4e4e7;border-radius:8px;padding:14px;white-space:pre-wrap;word-break:break-all;font-size:13px">${token}</pre>
      <p style="color:#71717a;font-size:13px">This is a one-time, perpetual unlock tied to your purchase.
         Keep this email — you can re-activate the key on any machine.</p>
    </div>`;
  const resp = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: env.EMAIL_FROM || "ORBIS <licenses@orbis.protolabs.studio>",
      to: [to],
      subject: "Your ORBIS customization unlock",
      html,
    }),
  });
  if (!resp.ok) {
    const body = await resp.text();
    console.log(`[email] resend failed: ${resp.status} ${body}`);
    return false;
  }
  return true;
}
