
/* ===== Avanza · Motor del reproductor de explicadores ===== */
(function(){
  if (window.initAvExplainers) return;
  window.__avTimers = [];
  function clearTimers(){ window.__avTimers.forEach(function(t){clearTimeout(t);}); window.__avTimers = []; }

  window.initAvExplainers = function(root){
    clearTimers();
    var scope = root || document;
    var boxes = scope.querySelectorAll('.av-explainer');
    for (var i=0;i<boxes.length;i++){ setup(boxes[i]); }
  };

  function setup(box){
    var slides = box.querySelectorAll('.av-slide');
    var total = slides.length;
    if (!total) return;
    var fill = box.querySelector('.av-progress-fill');
    var counter = box.querySelector('.av-counter');
    var dotsWrap = box.querySelector('.av-dots');
    var playBtn = box.querySelector('.av-btn.play');
    var prevBtn = box.querySelector('.av-btn.prev');
    var nextBtn = box.querySelector('.av-btn.next');

    var idx = 0, playing = true, timer = null, startTs = 0, remaining = 0;

    // dots
    var dots = [];
    if (dotsWrap){
      dotsWrap.innerHTML = '';
      for (var k=0;k<total;k++){
        var b = document.createElement('button');
        b.className = 'av-dot';
        b.setAttribute('aria-label','Ir a la diapositiva '+(k+1));
        (function(j){ b.addEventListener('click', function(){ goto(j); }); })(k);
        dotsWrap.appendChild(b);
        dots.push(b);
      }
    }

    function dur(i){ return parseInt(slides[i].getAttribute('data-dur')||'6500',10); }

    function paint(){
      for (var i=0;i<total;i++){ slides[i].classList.toggle('active', i===idx); }
      for (var d=0;d<dots.length;d++){ dots[d].classList.toggle('on', d===idx); }
      if (counter) counter.textContent = (idx+1)+' / '+total;
    }

    function resetBar(){
      if (!fill) return;
      fill.style.transition = 'none';
      fill.style.width = '0%';
      void fill.offsetWidth; // reflow
    }
    function runBar(ms){
      if (!fill) return;
      fill.style.transition = 'width '+ms+'ms linear';
      fill.style.width = '100%';
    }
    function freezeBar(){
      if (!fill) return;
      var w = getComputedStyle(fill).width;
      var pw = getComputedStyle(fill.parentElement).width;
      fill.style.transition = 'none';
      fill.style.width = (parseFloat(w)/parseFloat(pw)*100)+'%';
    }

    function schedule(ms){
      clearTimeout(timer);
      startTs = Date.now();
      remaining = ms;
      runBar(ms);
      timer = setTimeout(function(){
        if (idx < total-1){ idx++; show(true); }
        else { playing = false; setPlayIcon(); }
      }, ms);
      window.__avTimers.push(timer);
    }

    function show(autoplayProgress){
      paint();
      resetBar();
      if (playing){ schedule(dur(idx)); }
    }

    function goto(i){
      idx = (i+total)%total;
      playing = true; setPlayIcon();
      show(true);
    }

    function setPlayIcon(){
      if (!playBtn) return;
      playBtn.innerHTML = playing
        ? '<i class="fa-solid fa-pause"></i>'
        : '<i class="fa-solid fa-play"></i>';
      playBtn.setAttribute('aria-label', playing ? 'Pausar' : 'Reproducir');
    }

    function togglePlay(){
      playing = !playing;
      setPlayIcon();
      if (playing){
        // reanudar el tiempo restante de la diapositiva actual
        var elapsed = Date.now() - startTs;
        var rem = Math.max(800, remaining - elapsed);
        schedule(rem);
      } else {
        clearTimeout(timer);
        freezeBar();
      }
    }

    if (playBtn) playBtn.addEventListener('click', togglePlay);
    if (prevBtn) prevBtn.addEventListener('click', function(){ goto(idx-1); });
    if (nextBtn) nextBtn.addEventListener('click', function(){ goto(idx+1); });

    // arranque
    idx = 0; playing = true; setPlayIcon(); show(true);
  }
})();
