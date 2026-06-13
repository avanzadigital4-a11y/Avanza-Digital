
  let _ventaContrato = null;

  function abrirModalContrato(venta) {
    _ventaContrato = venta || {};
    document.getElementById('c_razon').value    = _ventaContrato.nombre_cliente || '';
    if (_ventaContrato.plan) document.getElementById('c_plan').value = _ventaContrato.plan;
    mcActualizarPais();
    document.getElementById('modalContrato').classList.add('abierto');
  }
  function cerrarModalContrato() {
    document.getElementById('modalContrato').classList.remove('abierto');
  }
  function mcToggleArs() {
    document.getElementById('mc_ars_tc').style.display =
      document.getElementById('c_moneda').value === 'ARS' ? 'block' : 'none';
  }

  // Terminología fiscal por país (refleja jarvis_contratos.IDENT_FISCAL_POR_PAIS).
  const FISCAL_PAIS = {
    'Argentina':  {empresa:'CUIT', persona:'DNI', ej:'30-XXXXXXXX-X'},
    'Mexico':     {empresa:'RFC', persona:'CURP / INE', ej:'ABC120101AB1'},
    'Peru':       {empresa:'RUC', persona:'DNI', ej:'20123456789'},
    'Chile':      {empresa:'RUT', persona:'RUN', ej:'76.123.456-7'},
    'Colombia':   {empresa:'NIT', persona:'Cédula', ej:'900.123.456-7'},
    'Costa Rica': {empresa:'Cédula jurídica', persona:'Cédula', ej:'3-101-123456'},
    'Venezuela':  {empresa:'RIF', persona:'Cédula', ej:'J-12345678-9'},
    'Uruguay':    {empresa:'RUT', persona:'C.I.', ej:'212345670019'},
    'Paraguay':   {empresa:'RUC', persona:'C.I.', ej:'80012345-6'},
    'Ecuador':    {empresa:'RUC', persona:'Cédula', ej:'1790012345001'},
    'Bolivia':    {empresa:'NIT', persona:'C.I.', ej:'1234567890'},
    'España':     {empresa:'CIF / NIF', persona:'DNI / NIE', ej:'B12345678'},
  };
  function mcActualizarPais() {
    const p = (document.getElementById('c_pais') || {}).value || 'Argentina';
    const f = FISCAL_PAIS[p] || {empresa:'Identificación tributaria', persona:'Documento de identidad', ej:''};
    const lc = document.getElementById('lbl_cuit'); if (lc) lc.textContent = f.empresa;
    const ic = document.getElementById('c_cuit');  if (ic) ic.placeholder = f.ej || f.empresa;
    const hc = document.getElementById('hint_cuit'); if (hc) hc.textContent = 'Identificación fiscal de la empresa — el equivalente al CUIT en ' + p + '.';
    const ld = document.getElementById('lbl_dni'); if (ld) ld.textContent = f.persona + ' del firmante';
    const idd = document.getElementById('c_dni'); if (idd) idd.placeholder = f.persona + ' de quien firma';
  }

  function mcToggleMant() {
    const on = document.getElementById('c_inc_mant').checked;
    document.getElementById('mc_mant_box').style.display = on ? 'flex' : 'none';
  }
  function mcSugerirMora() {
    const a = parseInt(document.getElementById('c_anticipo').value) || 100;
    if (a < 100) document.getElementById('c_inc_mora').checked = true;
  }

  async function generarContratoPDF() {
    const btn = document.getElementById('mc_gen');
    btn.disabled = true; btn.textContent = 'Generando…';
    try {
      const body = {
        cliente_razon_social: document.getElementById('c_razon').value,
        cliente_cuit:         document.getElementById('c_cuit').value,
        cliente_domicilio:    document.getElementById('c_domicilio').value,
        cliente_representante:document.getElementById('c_rep').value,
        cliente_cargo:        document.getElementById('c_cargo').value,
        cliente_email:        document.getElementById('c_email').value,
        cliente_pais:             document.getElementById('c_pais').value,
        cliente_condicion_fiscal: document.getElementById('c_condicion').value,
        cliente_dni:              document.getElementById('c_dni').value,
        numero_contrato:          document.getElementById('c_numero').value,
        incluir_mantenimiento:    document.getElementById('c_inc_mant').checked,
        plan_mantenimiento:       document.getElementById('c_plan_mant').value,
        mantenimiento_precio:     parseFloat(document.getElementById('c_mant_precio').value) || null,
        incluir_mora:             document.getElementById('c_inc_mora').checked,
        plan:                 document.getElementById('c_plan').value,
        moneda:               document.getElementById('c_moneda').value,
        tipo_cambio:          parseFloat(document.getElementById('c_tc').value) || null,
        factura_tipo:         document.getElementById('c_factura').value,
        iva_incluido:         document.getElementById('c_iva').value === 'true',
        anticipo_pct:         parseInt(document.getElementById('c_anticipo').value) || 100,
        ciudad:               document.getElementById('c_ciudad').value,
        formato:              document.getElementById('c_formato').value,
      };
      // Endpoint: usa la Venta si tiene id; si no, el preview ad-hoc.
      const url = (_ventaContrato && _ventaContrato.id)
        ? `${API}/ventas/${_ventaContrato.id}/contrato`
        : `${API}/contratos/preview`;

      const res = await apiFetch(url, { method: 'POST', body });
      if (!res.ok) {
        const txt = await res.text();
        alert('No se pudo generar el contrato: ' + txt);
        return;
      }
      const blob = await res.blob();
      const ext = body.formato === 'docx' ? 'docx' : 'pdf';
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `contrato_${(body.cliente_razon_social || 'cliente').replace(/\W+/g,'_')}.${ext}`;
      document.body.appendChild(link); link.click(); link.remove();

      cerrarModalContrato();
      // Opcional: abrir WhatsApp con un mensaje (el PDF se adjunta a mano, ya descargado)
      // window.open('https://wa.me/?text=' + encodeURIComponent('Te paso el contrato de Avanza Digital 📄'), '_blank');
    } catch (e) {
      alert('Error al generar el contrato: ' + e);
    } finally {
      btn.disabled = false; btn.textContent = 'Generar PDF';
    }
  }
