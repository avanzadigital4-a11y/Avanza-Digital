
/* ===== Sidebar: colapsar (PC) y drawer (mobile) — NO modifica la lógica existente ===== */
function avzCollapse(){ document.documentElement.classList.toggle('avz-collapsed'); }
function avzDrawer(open){ document.documentElement.classList.toggle('avz-drawer', !!open); }
document.addEventListener('click', function(e){
  var it = e.target.closest ? e.target.closest('#avz-sidebar .avz-nav-item') : null;
  if(it){ avzDrawer(false); }
}, false);
