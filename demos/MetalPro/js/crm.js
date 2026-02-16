/* METALPRO CRM INTELLIGENCE MODULE
   - Persistencia de UTMs (Fuente de tráfico)
   - Rastreo de navegación (Scoring de interés)
   - Inyección de datos en formularios
*/

const CRM_CONFIG = {
    storageKey: 'metalpro_user_journey',
    debug: false
};

// 1. INICIAR RASTREO AL CARGAR CUALQUIER PÁGINA
document.addEventListener('DOMContentLoaded', () => {
    saveTrafficSource();
    trackPageVisit();
});

// 2. GUARDAR FUENTE DE TRÁFICO (UTMs)
// Si el usuario llega con ?utm_source=google, lo guardamos para siempre.
function saveTrafficSource() {
    const urlParams = new URLSearchParams(window.location.search);
    let sessionData = getSessionData();

    // Solo sobrescribir si hay nuevos parámetros en la URL
    if (urlParams.has('utm_source')) {
        sessionData.source = {
            utm_source: urlParams.get('utm_source'),
            utm_medium: urlParams.get('utm_medium') || '',
            utm_campaign: urlParams.get('utm_campaign') || '',
            landing_page: window.location.pathname,
            timestamp: new Date().toISOString()
        };
        saveSessionData(sessionData);
        if(CRM_CONFIG.debug) console.log('CRM: Fuente de tráfico actualizada', sessionData.source);
    }
}

// 3. RASTREAR INTERESES (Para el Vendedor)
// Si visita "calidad.html", marcamos el flag de "Interés en Calidad"
function trackPageVisit() {
    const path = window.location.pathname;
    let sessionData = getSessionData();

    if (!sessionData.history) sessionData.history = [];
    
    // Evitar duplicados consecutivos
    if (sessionData.history[sessionData.history.length - 1] !== path) {
        sessionData.history.push(path);
    }

    // Detectar Intereses Clave
    if (path.includes('calidad')) sessionData.interests.calidad = true;
    if (path.includes('oil-gas') || path.includes('energia')) sessionData.interests.alta_exigencia = true;
    if (path.includes('agro')) sessionData.interests.agro = true;

    saveSessionData(sessionData);
}

// 4. FUNCIÓN PARA OBTENER DATOS LIMPIOS (Para usar en los Formularios)
function getCRMData() {
    const data = getSessionData();
    const historyString = data.history.join(' > '); // Ej: /index > /servicios > /cotizador
    
    return {
        // Datos de Fuente
        source: data.source.utm_source || 'Orgánico/Directo',
        medium: data.source.utm_medium || '',
        campaign: data.source.utm_campaign || '',
        
        // Datos de Comportamiento
        interes_calidad: data.interests.calidad ? "SI" : "NO",
        interes_exigencia: data.interests.alta_exigencia ? "SI" : "NO",
        historial_navegacion: historyString
    };
}

// UTILIDADES INTERNAS
function getSessionData() {
    const data = localStorage.getItem(CRM_CONFIG.storageKey);
    return data ? JSON.parse(data) : { source: {}, history: [], interests: {} };
}

function saveSessionData(data) {
    localStorage.setItem(CRM_CONFIG.storageKey, JSON.stringify(data));
}