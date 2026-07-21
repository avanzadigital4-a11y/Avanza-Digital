/* ============================================================================
   portal.chat.js — Chat de Comunidad Avanza (Sala General + Mensajes Directos)
   Carga perezosa: se baja recién al abrir la pestaña "Chat" (ver cargarModulo
   en portal.html). Centinela: inicializarChat (así cargarModulo no la vuelve
   a bajar si ya está en memoria).

   Sin websockets, igual que el resto del portal: todo por polling corto.
   El "de verdad llegó el aviso" ya lo resuelve el backend con
   notificar_aliado_multicanal (WhatsApp → email → campanita); este archivo
   solo se ocupa de que, mientras la pestaña esté abierta, se sienta en vivo.
   ========================================================================= */

   let _chatSubtabActual = 'sala';
   let _chatUltimoIdSala = 0;
   let _chatPollSala = null;
   let _chatPollConversaciones = null;
   let _chatDMActivo = null; // { codigo, nombre }
   
   function inicializarChat() {
     // Idempotente: si ya estaba armado (venís y volvés a la pestaña), solo
     // reengancha el polling en vez de reconstruir todo desde cero.
     chatCargarSala(true);
     chatCargarConversaciones();
     chatDetenerPolling(); // por si quedó algo de una visita anterior
     _chatPollSala = setInterval(() => { if (_chatSubtabActual === 'sala') chatCargarSala(false); }, 4000);
     _chatPollConversaciones = setInterval(chatCargarConversaciones, 6000);
     if (_chatDMActivo) _iniciarPollingDM();
   }
   
   function chatDetenerPolling() {
     if (_chatPollSala) { clearInterval(_chatPollSala); _chatPollSala = null; }
     if (_chatPollConversaciones) { clearInterval(_chatPollConversaciones); _chatPollConversaciones = null; }
     if (window._chatPollDM) { clearInterval(window._chatPollDM); window._chatPollDM = null; }
   }
   
   function chatCambiarSubtab(sub, btn) {
     _chatSubtabActual = sub;
     document.querySelectorAll('.chat-subtab-btn').forEach(b => b.classList.remove('activo'));
     if (btn) btn.classList.add('activo');
     document.getElementById('chat-panel-sala').style.display = sub === 'sala' ? '' : 'none';
     document.getElementById('chat-panel-dm').style.display = sub === 'dm' ? '' : 'none';
     if (sub === 'sala') chatCargarSala(false);
     else chatCargarConversaciones();
   }
   
   // ─── SALA GENERAL ────────────────────────────────────────────────────────────
   
   async function chatCargarSala(scrollAlFinal) {
     const cont = document.getElementById('chat-sala-mensajes');
     if (!cont) return;
     try {
       const url = `${API}/chat/sala/general${_chatUltimoIdSala ? '?despues_de=' + _chatUltimoIdSala : ''}`;
       const res = await apiFetch(url);
       if (!res.ok) return;
       const data = await res.json();
       if (!data.mensajes.length && cont.children.length) return; // nada nuevo, no repintar
   
       if (_chatUltimoIdSala === 0) cont.innerHTML = ''; // primera carga: limpiar el "cargando"
       if (!data.mensajes.length && !cont.children.length) {
         cont.innerHTML = `<div style="margin:auto;color:var(--text-dim);font-size:.82rem;text-align:center;">Todavía no hay mensajes. ¡Arrancá vos la charla!</div>`;
         return;
       }
       data.mensajes.forEach(m => {
         cont.insertAdjacentHTML('beforeend', _chatBurbuja(m));
         _chatUltimoIdSala = Math.max(_chatUltimoIdSala, m.id);
       });
       if (scrollAlFinal !== false) cont.scrollTop = cont.scrollHeight;
     } catch (e) { console.error('chat sala:', e); }
   }
   
   function _chatBurbuja(m) {
     const esMio = typeof aliado !== 'undefined' && aliado && m.remitente_codigo === aliado.codigo;
     const nombre = esMio ? '' : `<div style="font-size:.72rem;font-weight:800;color:var(--primary);margin-bottom:2px;">${escapeHtml(m.remitente_nombre)} <span style="color:var(--text-dim);font-weight:600;">· ${m.remitente_nivel || 'BASIC'}</span></div>`;
     return `<div class="chat-msg-row ${esMio ? 'mio' : ''}">
       <div class="chat-bubble">${nombre}${escapeHtml(m.cuerpo)}<div class="chat-meta">${m.fecha || ''}</div></div>
     </div>`;
   }
   
   async function chatEnviarSala() {
     const input = document.getElementById('chat-sala-input');
     const cuerpo = (input.value || '').trim();
     if (!cuerpo) return;
     input.value = '';
     input.disabled = true;
     try {
       const res = await apiFetch(`${API}/chat/sala/general/mensaje`, {
         method: 'POST', headers: { 'Content-Type': 'application/json' },
         body: JSON.stringify({ cuerpo })
       });
       if (!res.ok) { const d = await res.json().catch(() => ({})); mostrarToast(d.detail || 'No se pudo enviar', 'red'); input.value = cuerpo; }
       else await chatCargarSala(true);
     } catch (e) { mostrarToast('Error de conexión.', 'red'); input.value = cuerpo; }
     input.disabled = false;
     input.focus();
   }
   
   // ─── MENSAJES DIRECTOS ────────────────────────────────────────────────────────
   
   async function chatCargarConversaciones() {
     const cont = document.getElementById('chat-conversaciones');
     if (!cont) return;
     try {
       const res = await apiFetch(`${API}/chat/conversaciones`);
       if (!res.ok) return;
       const data = await res.json();
   
       const totalNoLeidos = data.total_no_leidos || 0;
       const subtabBadge = document.getElementById('comunidad-chat-badge');
       const dmBadge = document.getElementById('chat-dm-badge');
       [subtabBadge, dmBadge].forEach(b => {
         if (!b) return;
         if (totalNoLeidos > 0) { b.style.display = ''; b.textContent = totalNoLeidos > 9 ? '9+' : totalNoLeidos; }
         else b.style.display = 'none';
       });
   
       if (!data.conversaciones.length) {
         cont.innerHTML = `<div style="color:var(--text-dim);font-size:.78rem;padding:12px 4px;">Todavía no tenés conversaciones. Buscá a alguien por su código para empezar.</div>`;
         return;
       }
       cont.innerHTML = data.conversaciones.map(c => `
         <div class="chat-conv-item ${_chatDMActivo && _chatDMActivo.codigo === c.codigo ? 'activo' : ''}" onclick="chatAbrirDM('${c.codigo}','${escapeHtml(c.nombre)}')">
           <div style="flex:1;min-width:0;">
             <div style="font-size:.84rem;font-weight:800;display:flex;align-items:center;gap:6px;">${escapeHtml(c.nombre)} <span style="font-size:.65rem;color:var(--text-dim);font-weight:600;">${c.nivel || ''}</span></div>
             <div style="font-size:.76rem;color:var(--text-dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${c.ultimo_es_mio ? 'Vos: ' : ''}${escapeHtml(c.ultimo_mensaje)}</div>
           </div>
           ${c.no_leidos ? `<div class="chat-badge-count">${c.no_leidos}</div>` : ''}
         </div>`).join('');
     } catch (e) { console.error('chat conversaciones:', e); }
   }
   
   function chatAbrirDMPorCodigo() {
     const input = document.getElementById('chat-dm-buscar-codigo');
     const codigo = (input.value || '').trim().toUpperCase();
     if (!codigo) return;
     if (typeof aliado !== 'undefined' && aliado && codigo === aliado.codigo) {
       mostrarToast('Ese es tu propio código 🙂', 'red');
       return;
     }
     input.value = '';
     chatAbrirDM(codigo, codigo);
   }
   
   async function chatAbrirDM(codigo, nombre) {
     _chatDMActivo = { codigo, nombre };
     document.getElementById('chat-dm-header').textContent = `Chat con ${nombre}`;
     document.getElementById('chat-dm-input').disabled = false;
     document.getElementById('chat-dm-btn-enviar').disabled = false;
     document.querySelectorAll('.chat-conv-item').forEach(el => el.classList.remove('activo'));
   
     await _chatCargarHistorialDM(true);
     chatCargarConversaciones(); // refleja que se leyó (baja el contador)
     _iniciarPollingDM();
   }
   
   function _iniciarPollingDM() {
     if (window._chatPollDM) clearInterval(window._chatPollDM);
     window._chatPollDM = setInterval(() => {
       if (_chatDMActivo && _chatSubtabActual === 'dm') _chatCargarHistorialDM(false);
     }, 4000);
   }
   
   async function _chatCargarHistorialDM(mostrarCargando) {
     if (!_chatDMActivo) return;
     const cont = document.getElementById('chat-dm-mensajes');
     if (mostrarCargando) cont.innerHTML = `<div style="margin:auto;color:var(--text-dim);font-size:.82rem;">Cargando...</div>`;
     try {
       const res = await apiFetch(`${API}/chat/dm/${encodeURIComponent(_chatDMActivo.codigo)}`);
       if (!res.ok) { cont.innerHTML = `<div style="margin:auto;color:var(--text-dim);font-size:.82rem;">No se encontró a ese aliado.</div>`; return; }
       const data = await res.json();
       if (!data.mensajes.length) {
         cont.innerHTML = `<div style="margin:auto;color:var(--text-dim);font-size:.82rem;text-align:center;">Todavía no se escribieron. ¡Mandale un saludo!</div>`;
         return;
       }
       cont.innerHTML = data.mensajes.map(_chatBurbuja).join('');
       cont.scrollTop = cont.scrollHeight;
     } catch (e) { console.error('chat dm:', e); }
   }
   
   async function chatEnviarDM() {
     if (!_chatDMActivo) return;
     const input = document.getElementById('chat-dm-input');
     const cuerpo = (input.value || '').trim();
     if (!cuerpo) return;
     input.value = '';
     input.disabled = true;
     try {
       const res = await apiFetch(`${API}/chat/dm/${encodeURIComponent(_chatDMActivo.codigo)}/mensaje`, {
         method: 'POST', headers: { 'Content-Type': 'application/json' },
         body: JSON.stringify({ cuerpo })
       });
       if (!res.ok) { const d = await res.json().catch(() => ({})); mostrarToast(d.detail || 'No se pudo enviar', 'red'); input.value = cuerpo; }
       else { await _chatCargarHistorialDM(false); chatCargarConversaciones(); }
     } catch (e) { mostrarToast('Error de conexión.', 'red'); input.value = cuerpo; }
     input.disabled = false;
     input.focus();
   }