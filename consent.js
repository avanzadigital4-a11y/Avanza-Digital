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
     + '@keyframes avzIn{from{opacity:0;transform:translate(-50%,16px)}to{opacity:1;transform:translate(-50%,0)}}'
     + '.avz-consent{position:fixed;left:50%;bottom:20px;transform:translate(-50%,0);z-index:99999;'
     + 'width:92%;max-width:640px;margin:0;display:flex;gap:16px;align-items:flex-start;'
     + 'background:rgba(15,15,15,.92);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);'
     + 'color:#e5e7eb;border:1px solid rgba(255,255,255,.08);border-radius:20px;padding:20px 22px;'
     + 'box-shadow:0 20px 60px rgba(0,0,0,.5);font-family:Inter,system-ui,Arial,sans-serif;font-size:14px;line-height:1.55;'
     + 'animation:avzIn .45s cubic-bezier(.16,1,.3,1)}'
     + '.avz-consent .avz-icon{flex:none;width:38px;height:38px;border-radius:12px;'
     + 'background:rgba(59,130,246,.12);border:1px solid rgba(59,130,246,.3);'
     + 'display:flex;align-items:center;justify-content:center}'
     + '.avz-consent .avz-icon svg{width:20px;height:20px}'
     + '.avz-consent .avz-body{flex:1;min-width:0}'
     + '.avz-consent h3{margin:0 0 4px;font-size:14.5px;font-weight:700;color:#fff;letter-spacing:-.2px}'
     + '.avz-consent p{margin:0 0 14px;color:#a1a1aa}'
     + '.avz-consent a{color:#60a5fa;text-decoration:none;border-bottom:1px solid rgba(96,165,250,.4)}'
     + '.avz-consent a:hover{border-color:#60a5fa}'
     + '.avz-consent .avz-actions{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end}'
     + '.avz-consent button{cursor:pointer;border:0;border-radius:30px;padding:10px 20px;'
     + 'font-weight:700;font-size:13.5px;font-family:inherit;transition:.2s ease}'
     + '.avz-acc{background:#3b82f6;color:#fff;box-shadow:0 6px 18px rgba(59,130,246,.35)}'
     + '.avz-acc:hover{background:#2563eb;transform:translateY(-1px);box-shadow:0 8px 22px rgba(59,130,246,.45)}'
     + '.avz-rej{background:rgba(255,255,255,.05);color:#d4d4d8;border:1px solid rgba(255,255,255,.12)!important}'
     + '.avz-rej:hover{background:rgba(255,255,255,.09);color:#fff}'
     + '@media(max-width:560px){.avz-consent{flex-direction:column;padding:18px;bottom:12px}'
     + '.avz-consent .avz-icon{display:none}'
     + '.avz-consent .avz-actions{justify-content:stretch;width:100%}.avz-consent button{flex:1}}';
    var st=document.createElement('style'); st.textContent=css; document.head.appendChild(st);
  
    var box=document.createElement('div');
    box.className='avz-consent'; box.setAttribute('role','dialog'); box.setAttribute('aria-label','Aviso de cookies');
    box.innerHTML =
      '<div class="avz-icon">'
      + '<svg viewBox="0 0 24 24" fill="none" stroke="#60a5fa" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
      + '<path d="M12 2a10 10 0 1 0 9.54 13.06 5 5 0 0 1-6.6-6.6A5 5 0 0 1 12 2Z"/>'
      + '<circle cx="8.5" cy="10.5" r="1" fill="#60a5fa" stroke="none"/>'
      + '<circle cx="12" cy="15.5" r="1" fill="#60a5fa" stroke="none"/>'
      + '<circle cx="15.5" cy="9.5" r="1" fill="#60a5fa" stroke="none"/>'
      + '</svg>'
      + '</div>'
      + '<div class="avz-body">'
      + '<h3>Usamos cookies</h3>'
      + '<p>Propias y de terceros, para medir el tr\u00e1fico y mejorar tu experiencia en el sitio. '
      + 'Pod\u00e9s aceptarlas o rechazarlas cuando quieras. M\u00e1s info en nuestra '
      + '<a href="/politica.html">pol\u00edtica de privacidad</a>.</p>'
      + '<div class="avz-actions">'
      + '<button class="avz-rej" type="button">Rechazar</button>'
      + '<button class="avz-acc" type="button">Aceptar</button>'
      + '</div>'
      + '</div>';
    function mount(){ document.body.appendChild(box);
      box.querySelector('.avz-acc').addEventListener('click',function(){ apply('granted'); box.remove(); });
      box.querySelector('.avz-rej').addEventListener('click',function(){ apply('denied'); box.remove(); });
    }
    if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);
  })();