// ============================================================
//  Cloudflare Worker — Proxy seguro para PageSpeed + Anthropic
//  Variables de entorno necesarias (Settings → Secrets):
//    avanzadigital  → tu key de console.anthropic.com
//    GOOGLE_KEY     → tu key de console.cloud.google.com
// ============================================================

export default {
  async fetch(request, env) {

    const ALLOWED_ORIGIN = '*'; // en producción: 'https://avanzadigital.digital'

    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
          'Access-Control-Allow-Methods': 'POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type',
          'Access-Control-Max-Age': '86400',
        },
      });
    }

    if (request.method !== 'POST') {
      return new Response('Método no permitido', { status: 405 });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response('Body inválido', { status: 400 });
    }

    // ── RUTA 1: PageSpeed Insights ──────────────────────────
    if (body.type === 'pagespeed') {
      const { url, strategy } = body;
      if (!url || !strategy) {
        return new Response('Faltan url o strategy', { status: 400 });
      }

      const psiUrl = `https://www.googleapis.com/pagespeedonline/v5/runPagespeed`
        + `?url=${encodeURIComponent(url)}`
        + `&strategy=${strategy}`
        + `&category=performance&category=seo&category=accessibility&category=best-practices`
        + `&key=${env.GOOGLE_KEY}`;

      const psiRes = await fetch(psiUrl);
      const psiData = await psiRes.json();

      return new Response(JSON.stringify(psiData), {
        status: psiRes.status,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
        },
      });
    }

    // ── RUTA 2: Anthropic AI ─────────────────────────────────
    if (body.type === 'ai') {
      const { prompt } = body;
      if (!prompt) {
        return new Response('Falta el campo "prompt"', { status: 400 });
      }

      const aiRes = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': env.avanzadigital,
          'anthropic-version': '2023-06-01',
        },
        body: JSON.stringify({
          model: 'claude-sonnet-4-6',
          max_tokens: 4000,
          messages: [{ role: 'user', content: prompt }],
        }),
      });

      const aiData = await aiRes.json();

      return new Response(JSON.stringify(aiData), {
        status: aiRes.status,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
        },
      });
    }

    return new Response('Tipo de request desconocido', { status: 400 });
  },
};


// ============================================================
//  PASOS PARA ACTUALIZAR
// ============================================================
//
//  1. Cloudflare → Workers & Pages → tu Worker → Edit
//     Reemplazá el código con este archivo → Deploy
//
//  2. Agregá el secreto de Google:
//     Settings → Variables and Secrets → Add
//     Name: GOOGLE_KEY  |  Type: Secret  |  Value: tu API key de Google
//     (la de Anthropic ya la tenés guardada como "avanzadigital")
//
//  3. Listo — ninguna key queda visible en el HTML
//
// ============================================================
