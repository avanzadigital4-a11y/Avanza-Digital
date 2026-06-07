/* Avanza Digital — Banner de consentimiento + Google Consent Mode v2
   Autocontenido. Trabaja con GTM (GTM-P499M76P). El estado por defecto
   (denied) se setea con el snippet inline ANTES de GTM. Este archivo solo
   pinta el banner y, según la elección, hace gtag('consent','update',...). */
(function () {
  if (window.__avzConsentLoaded) return; window.__avzConsentLoaded = true;
  var KEY = 'avz_consent';
  window.dataLayer = window.dataLayer || [];
  function gtag(){ dataLayer.push(arguments); }

  function apply(state){
    gtag('consent','update',{
      ad_storage: state, analytics_storage: state,
      ad_user_data: state, ad_personalization: state
    });
    try { localStorage.setItem(KEY, state); } catch(e){}
    dataLayer.push({event:'consent_'+(state==='granted'?'accepted':'rejected')});
  }

  // si ya eligió, no mostrar banner
  var saved=null; try{ saved=localStorage.getItem(KEY);}catch(e){}
  if (saved==='granted' || saved==='denied') return;

  var css = ''
   + '.avz-consent{position:fixed;left:16px;right:16px;bottom:16px;z-index:99999;max-width:760px;margin:0 auto;'
   + 'background:#0f172a;color:#e2e8f0;border:1px solid #1e293b;border-radius:14px;padding:18px 20px;'
   + 'box-shadow:0 10px 40px rgba(0,0,0,.35);font-family:Inter,system-ui,Arial,sans-serif;font-size:14px;line-height:1.5}'
   + '.avz-consent p{margin:0 0 12px}'
   + '.avz-consent a{color:#60a5fa;text-decoration:underline}'
   + '.avz-consent .avz-actions{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end}'
   + '.avz-consent button{cursor:pointer;border:0;border-radius:9px;padding:10px 18px;font-weight:600;font-size:14px;font-family:inherit}'
   + '.avz-acc{background:#2563eb;color:#fff}.avz-acc:hover{background:#1d4ed8}'
   + '.avz-rej{background:transparent;color:#cbd5e1;border:1px solid #334155!important}.avz-rej:hover{background:#1e293b}'
   + '@media(max-width:520px){.avz-consent .avz-actions{justify-content:stretch}.avz-consent button{flex:1}}';
  var st=document.createElement('style'); st.textContent=css; document.head.appendChild(st);

  var box=document.createElement('div');
  box.className='avz-consent'; box.setAttribute('role','dialog'); box.setAttribute('aria-label','Aviso de cookies');
  box.innerHTML =
    '<p>Usamos cookies propias y de terceros para medir el tr\u00e1fico y mejorar tu experiencia. '
    + 'Pod\u00e9s aceptarlas o rechazarlas. M\u00e1s info en nuestra '
    + '<a href="/politica.html">pol\u00edtica de privacidad</a>.</p>'
    + '<div class="avz-actions">'
    + '<button class="avz-rej" type="button">Rechazar</button>'
    + '<button class="avz-acc" type="button">Aceptar</button>'
    + '</div>';
  function mount(){ document.body.appendChild(box);
    box.querySelector('.avz-acc').addEventListener('click',function(){ apply('granted'); box.remove(); });
    box.querySelector('.avz-rej').addEventListener('click',function(){ apply('denied'); box.remove(); });
  }
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);
})();
