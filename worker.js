// ============================================================
//  Cloudflare Worker — Proxy seguro para PageSpeed + Anthropic
//  Variables de entorno necesarias (Settings → Secrets):
//    avanzadigital  → tu key de console.anthropic.com
//    GOOGLE_KEY     → tu key de console.cloud.google.com
//  Variables de entorno opcionales:
//    AI_MODEL_FAST  → modelo "barato" (default: claude-haiku-4-5-20251001)
//    AI_MODEL_HEAVY → modelo "caro" para razonamiento (default: claude-sonnet-4-6)
// ============================================================

const DEFAULT_FAST_MODEL  = 'claude-haiku-4-5-20251001';
const DEFAULT_HEAVY_MODEL = 'claude-sonnet-4-6';

export default {
  async fetch(request, env) {

    // SEGURIDAD: origin cerrado al dominio de producción. Con '*' cualquier
    // sitio podía llamar a este Worker desde el navegador y quemar la cuota
    // de Anthropic/PageSpeed. Para probar en local, agregá temporalmente tu
    // origin de dev acá (y sacalo antes de deployar).
    const ALLOWED_ORIGINS = [
      'https://avanzadigital.digital',
      'https://www.avanzadigital.digital',
    ];
    const reqOrigin = request.headers.get('Origin') || '';
    const ALLOWED_ORIGIN = ALLOWED_ORIGINS.includes(reqOrigin) ? reqOrigin : ALLOWED_ORIGINS[0];

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
      return new Response('Metodo no permitido', { status: 405 });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response('Body invalido', { status: 400 });
    }

    // -- RUTA 1: PageSpeed Insights -------------------------
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

    // -- RUTA 2: Anthropic AI -------------------------------
    //
    // Modelo por defecto: Haiku 4.5 (5-10x mas barato que Sonnet, calidad
    // equivalente para tareas cortas: resumir un analisis de PageSpeed,
    // sugerencias rapidas en la comunidad, redactar 1-2 parrafos, etc.).
    //
    // Para razonamiento mas pesado (analisis multi-paso, generacion larga,
    // toma de decisiones que requiere chain-of-thought), el cliente puede
    // pedir explicitamente { "mode": "heavy" } y se usa Sonnet 4.6. Tambien
    // se acepta { "model": "<id-exacto>" } como override directo.
    //
    if (body.type === 'ai') {
      const { prompt, mode, model, max_tokens } = body;
      if (!prompt) {
        return new Response('Falta el campo "prompt"', { status: 400 });
      }

      const fastModel  = env.AI_MODEL_FAST  || DEFAULT_FAST_MODEL;
      const heavyModel = env.AI_MODEL_HEAVY || DEFAULT_HEAVY_MODEL;
      const chosenModel = model || (mode === 'heavy' ? heavyModel : fastModel);

      const aiRes = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': env.avanzadigital,
          'anthropic-version': '2023-06-01',
        },
        body: JSON.stringify({
          model: chosenModel,
          max_tokens: Number.isInteger(max_tokens) ? max_tokens : 4000,
          messages: [{ role: 'user', content: prompt }],
        }),
      });

      const aiData = await aiRes.json();

      return new Response(JSON.stringify(aiData), {
        status: aiRes.status,
        headers: {
          'Content-Type': 'application/json',
          'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
          'X-Avanza-AI-Model': chosenModel,
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
//  1. Cloudflare > Workers & Pages > tu Worker > Edit
//     Reemplaza el codigo con este archivo > Deploy
//
//  2. Agrega el secreto de Google:
//     Settings > Variables and Secrets > Add
//     Name: GOOGLE_KEY  |  Type: Secret  |  Value: tu API key de Google
//     (la de Anthropic ya la tenes guardada como "avanzadigital")
//
//  3. (Opcional) Para forzar otro modelo sin tocar codigo:
//     Settings > Variables and Secrets > Add
//     AI_MODEL_FAST  = claude-haiku-4-5-20251001
//     AI_MODEL_HEAVY = claude-sonnet-4-6
//
//  4. Listo - ninguna key queda visible en el HTML
//
//  -- COMO PEDIR DESDE EL FRONT -------------------------------
//  Por defecto (Haiku, barato):
//     fetch(worker, { method:'POST', body:JSON.stringify({
//        type:'ai', prompt:'Resumi esto en 3 bullets: ...'
//     })})
//  Para tareas pesadas (Sonnet):
//     body:JSON.stringify({ type:'ai', mode:'heavy', prompt:'...' })
//  Override directo del modelo:
//     body:JSON.stringify({ type:'ai', model:'claude-opus-4-7', prompt:'...' })
//
// ============================================================