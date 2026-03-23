#!/usr/bin/env python3
"""
apply_patch.py — Parche de seguridad para admin.html y portal.html
Ejecutar desde la carpeta avanza-portal/:

    python3 apply_patch.py

Qué hace:
  admin.html  → Agrega campo API Key en el login + envía X-API-Key en todos los fetch()
  portal.html → Cambia login para usar /aliados/login (verifica contraseña real)
"""

import re, sys, shutil
from pathlib import Path

BASE = Path(__file__).parent

# ─────────────────────────────────────────────────────────────────
# PATCH 1: admin.html
# ─────────────────────────────────────────────────────────────────

def patch_admin():
    path = BASE / "admin.html"
    if not path.exists():
        print("ERROR: admin.html no encontrado"); sys.exit(1)

    shutil.copy(path, path.with_suffix(".html.bak"))
    html = path.read_text(encoding="utf-8")

    # 1a. Agregar campo "API Key" después del campo de contraseña en el login
    campo_pass = '<input type="password" id="login-pass"'
    if 'id="login-apikey"' not in html:
        bloque_apikey = '''
    <div class="field">
      <label>API Key de admin</label>
      <input type="password" id="login-apikey" placeholder="••••••••" autocomplete="off">
    </div>'''
        # Buscar el div.field que contiene login-pass y agregar después
        html = html.replace(
            '    <button class="btn-admin" onclick="loginAdmin()"',
            bloque_apikey + '\n    <button class="btn-admin" onclick="loginAdmin()"'
        )
        print("  ✓ Campo API Key agregado al formulario de login")
    else:
        print("  · Campo API Key ya existe")

    # 1b. Agregar variable global apiKey y modificar loginAdmin()
    old_vars = "let todosAliados = [];"
    new_vars = "let todosAliados = [];\nlet _apiKey = '';"
    html = html.replace(old_vars, new_vars, 1)

    # 1c. Reemplazar loginAdmin() para capturar la API key
    old_login = """function loginAdmin() {
  const user = document.getElementById('login-user').value.trim();
  const pass = document.getElementById('login-pass').value;
  const err  = document.getElementById('login-error');
  err.style.display = 'none';
  if (_authOk(user, pass)) {
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('admin-screen').style.display = 'block';
    cargarTodo();
  } else {
    err.style.display = 'block';
  }
}"""
    new_login = """function loginAdmin() {
  const user = document.getElementById('login-user').value.trim();
  const pass = document.getElementById('login-pass').value;
  const apikey = document.getElementById('login-apikey') ? document.getElementById('login-apikey').value.trim() : '';
  const err  = document.getElementById('login-error');
  err.style.display = 'none';
  if (_authOk(user, pass)) {
    _apiKey = apikey;
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('admin-screen').style.display = 'block';
    cargarTodo();
  } else {
    err.style.display = 'block';
  }
}"""
    html = html.replace(old_login, new_login, 1)
    print("  ✓ loginAdmin() actualizado para capturar API key")

    # 1d. Agregar helper apiFetch() antes de cargarTodo()
    helper = """
// ── API FETCH CON AUTH ──────────────────────────────────────────────────────
function apiFetch(url, opts = {}) {
  opts.headers = opts.headers || {};
  if (_apiKey) opts.headers['X-API-Key'] = _apiKey;
  return fetch(url, opts);
}

"""
    html = html.replace(
        "async function cargarTodo()",
        helper + "async function cargarTodo()"
    )
    print("  ✓ Helper apiFetch() agregado")

    # 1e. Reemplazar fetch(`${API}/... por apiFetch(`${API}/...
    count = html.count("fetch(`${API}/")
    html = html.replace("fetch(`${API}/", "apiFetch(`${API}/")
    print(f"  ✓ {count} llamadas fetch(`${{API}}/...) → apiFetch()")

    # 1f. Reemplazar await fetch(url, ...) — cubre funciones como crearAliado()
    #     que construyen la URL en variable antes de llamar fetch.
    count2 = html.count("await fetch(url,")
    html = html.replace("await fetch(url,", "await apiFetch(url,")
    print(f"  ✓ {count2} llamadas await fetch(url, ...) → apiFetch()")

    # Verificación final
    import re as _re
    sobrantes = _re.findall(r'await fetch\(', html)
    if sobrantes:
        print(f"  ⚠ ATENCIÓN: quedan {len(sobrantes)} llamadas 'await fetch()' sin parchear.")
    else:
        print("  ✓ Verificación: no quedan fetch() sin parchear")

    # 1g. Nota sobre _authOk
    print("  ℹ _authOk sigue siendo validación client-side. La API Key protege")
    print("    todos los endpoints — _authOk es solo cosmético en este parche.")
    print("    Para eliminarlo: agregar POST /admin/login al backend (futuro).")

    path.write_text(html, encoding="utf-8")
    print(f"  ✓ admin.html parcheado (backup: admin.html.bak)")


# ─────────────────────────────────────────────────────────────────
# PATCH 2: portal.html
# ─────────────────────────────────────────────────────────────────

def patch_portal():
    path = BASE / "portal.html"
    if not path.exists():
        print("ERROR: portal.html no encontrado"); sys.exit(1)

    shutil.copy(path, path.with_suffix(".html.bak"))
    html = path.read_text(encoding="utf-8")

    # 2a. Reemplazar iniciarSesion() para usar /aliados/login con contraseña
    old_fn = """async function iniciarSesion() {
  const codigo = document.getElementById('login-codigo').value.trim().toUpperCase();
  const btn = document.getElementById('btn-login');
  const err = document.getElementById('login-error');
  err.style.display = 'none';
  if (!codigo) { err.style.display = 'block'; return; }
  btn.innerHTML = '<span class="spinner"></span> Verificando...';
  btn.disabled = true;
  try {
    const res = await fetch(`${API}/aliados/${codigo}`);
    if (!res.ok) throw new Error();
    aliado = await res.json();
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('portal-screen').style.display = 'block';
    cargarTodo();
  } catch { err.style.display = 'block'; }
  btn.innerHTML = '<i class="fa-solid fa-arrow-right-to-bracket"></i> Ingresar al portal';
  btn.disabled = false;
}"""

    new_fn = """async function iniciarSesion() {
  const codigo = document.getElementById('login-codigo').value.trim().toUpperCase();
  const pass   = document.getElementById('login-pass').value;
  const btn = document.getElementById('btn-login');
  const err = document.getElementById('login-error');
  err.style.display = 'none';
  if (!codigo || !pass) { err.style.display = 'block'; return; }
  btn.innerHTML = '<span class="spinner"></span> Verificando...';
  btn.disabled = true;
  try {
    const url = `${API}/aliados/login?codigo=${encodeURIComponent(codigo)}&password=${encodeURIComponent(pass)}`;
    const res = await fetch(url, { method: 'POST' });
    if (!res.ok) throw new Error();
    aliado = await res.json();
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('portal-screen').style.display = 'block';
    cargarTodo();
  } catch {
    err.style.display = 'block';
  }
  btn.innerHTML = '<i class="fa-solid fa-arrow-right-to-bracket"></i> Ingresar al portal';
  btn.disabled = false;
}"""

    if old_fn in html:
        html = html.replace(old_fn, new_fn)
        print("  ✓ iniciarSesion() actualizado — ahora verifica contraseña real")
    else:
        print("  ⚠ No se encontró iniciarSesion() exacto. Verificar manualmente.")

    # 2b. cargarTodo del portal también debe pasar contraseña — pero no la tenemos.
    # En vez de re-fetch por codigo, usamos los datos ya cargados del login.
    old_cargar = "  try { const res = await fetch(`${API}/aliados/${aliado.codigo}`); aliado = await res.json(); } catch {}"
    new_cargar = "  try { const res = await fetch(`${API}/aliados/${aliado.codigo}`); if(res.ok) aliado = await res.json(); } catch {}"
    html = html.replace(old_cargar, new_cargar)

    path.write_text(html, encoding="utf-8")
    print(f"  ✓ portal.html parcheado (backup: portal.html.bak)")


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n── Parcheando admin.html ──────────────────────────")
    patch_admin()
    print("\n── Parcheando portal.html ─────────────────────────")
    patch_portal()
    print("\n✅ Parche aplicado. Próximos pasos:")
    print("   1. Configurar ADMIN_API_KEY en Railway → Settings → Variables")
    print("   2. Hacer push del nuevo main.py")
    print("   3. Llamar a POST /admin/setup?username=ivan&password=TU_PASS (con X-API-Key)")
    print("   4. Subir admin.html y portal.html parcheados\n")