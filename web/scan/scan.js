/*!
 * scan.js — UI/камера для cm-scan (CYRQODE Circle Mark), v0.1.
 * Использует ТОЛЬКО cm-scan-core.js (тот же файл, что и selftest.html) для
 * распознавания геометрии — здесь только "склейка" с камерой/интерфейсом.
 * Никаких внешних библиотек.
 */
(function () {
  "use strict";

  var EXPECTED_NAMESPACE_INFO = null; // не проверяем реестр — просто предупреждаем в тексте
  // Адрес реестра знает СКАНЕР, а не марка: в марку зашит лишь код резолвера
  // (см. resolver_hint). Поэтому переезд реестра не обесценивает выпущенные
  // марки — обновляется только эта таблица.
  // Человеку показываем СТРАНИЦУ записи, а не JSON: json — формат для машин.
  // Сырые данные доступны отдельной ссылкой на самой странице.
  var REGISTRY_BY_RESOLVER = {
    "default": "https://bizdnai.com/cyrqode/r/",
    "public-registry": "https://bizdnai.com/cyrqode/r/",
  };
  var RAW_REGISTRY_BASE = "https://github.com/Kabzhanov/cyrqode/blob/main/registry/";
  var GITHUB_REGISTRY_BASE = REGISTRY_BY_RESOLVER["default"];

  function registryUrlFor(payload) {
    var base = (payload && REGISTRY_BY_RESOLVER[payload.resolver_hint]) || GITHUB_REGISTRY_BASE;
    return base + (payload ? payload.entity_id : "") + "/";
  }

  function rawRecordUrlFor(payload) {
    return RAW_REGISTRY_BASE + (payload ? payload.entity_id : "") + ".json";
  }

  var els = {
    app: document.getElementById("app"),
    screens: {
      start: document.getElementById("screen-start"),
      camera: document.getElementById("screen-camera"),
      error: document.getElementById("screen-error"),
      result: document.getElementById("screen-result"),
    },
    btnStartScan: document.getElementById("btn-start-scan"),
    btnPickPhoto: document.getElementById("btn-pick-photo"),
    fileInput: document.getElementById("file-input"),
    video: document.getElementById("video"),
    captureCanvas: document.getElementById("capture-canvas"),
    crosshair: document.getElementById("crosshair"),
    cameraStatus: document.getElementById("camera-status"),
    btnCameraBack: document.getElementById("btn-camera-back"),
    btnCameraFile: document.getElementById("btn-camera-file"),
    errorText: document.getElementById("error-text"),
    btnOpenBrowser: document.getElementById("btn-open-browser"),
    btnErrorFile: document.getElementById("btn-error-file"),
    btnErrorBack: document.getElementById("btn-error-back"),
    resultIcon: document.getElementById("result-icon"),
    resultTitle: document.getElementById("result-title"),
    resultText: document.getElementById("result-text"),
    resultFields: document.getElementById("result-fields"),
    resultActions: document.getElementById("result-actions"),
    btnResultBack: document.getElementById("btn-result-back"),
  };

  var state = {
    stream: null,
    loopTimer: null,
    consecutiveCrcFail: 0,
    locked: false,
  };

  function showScreen(name) {
    Object.keys(els.screens).forEach(function (k) {
      els.screens[k].classList.toggle("active", k === name);
    });
    els.app.classList.toggle("camera-mode", name === "camera");
  }

  // =========================================================================
  // Камера: старт/стоп
  // =========================================================================
  function stopCamera() {
    if (state.loopTimer) { clearInterval(state.loopTimer); state.loopTimer = null; }
    if (state.stream) {
      state.stream.getTracks().forEach(function (t) { t.stop(); });
      state.stream = null;
    }
  }

  function startCameraFlow() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showCameraUnavailable(
        "Этот браузер/окно не поддерживает доступ к камере (getUserMedia недоступен)."
      );
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false })
      .then(function (stream) {
        state.stream = stream;
        els.video.srcObject = stream;
        showScreen("camera");
        state.consecutiveCrcFail = 0;
        state.locked = false;
        setCameraStatus("Наведите CYRQODE в прицел");
        els.video.play().catch(function () {});
        els.video.onloadedmetadata = function () {
          startScanLoop();
        };
      })
      .catch(function (err) {
        var msg =
          "Доступ к камере не выдан или недоступен (" +
          (err && err.name ? err.name : String(err)) +
          "). Частый случай — приложение открыло страницу во встроенном просмотрщике " +
          "(Android WebView) без разрешения на камеру.";
        showCameraUnavailable(msg);
      });
  }

  function showCameraUnavailable(msg) {
    els.errorText.textContent = msg;
    els.btnOpenBrowser.href = location.href;
    showScreen("error");
  }

  function setCameraStatus(text) {
    els.cameraStatus.textContent = text;
  }

  // =========================================================================
  // Отображение видео на экране — object-fit: cover, поэтому кадр в
  // нативных пикселях видео не совпадает 1:1 с CSS-пикселями прицела.
  // Стандартная формула перевода CSS-точки в "родные" пиксели видео при
  // object-fit: cover.
  // =========================================================================
  function cssToVideoPoint(cssX, cssY, cssW, cssH, videoW, videoH) {
    var k = Math.max(cssW / videoW, cssH / videoH);
    var displayedW = videoW * k, displayedH = videoH * k;
    var offsetX = (displayedW - cssW) / 2;
    var offsetY = (displayedH - cssH) / 2;
    return { x: (cssX + offsetX) / k, y: (cssY + offsetY) / k, scale: 1 / k };
  }

  function getCrosshairVideoGeometry() {
    var rect = els.crosshair.getBoundingClientRect();
    var videoRect = els.video.getBoundingClientRect();
    var cssCx = rect.left + rect.width / 2 - videoRect.left;
    var cssCy = rect.top + rect.height / 2 - videoRect.top;
    var cssR = rect.width / 2;
    var videoW = els.video.videoWidth, videoH = els.video.videoHeight;
    if (!videoW || !videoH) return null;
    var center = cssToVideoPoint(cssCx, cssCy, videoRect.width, videoRect.height, videoW, videoH);
    var edge = cssToVideoPoint(cssCx + cssR, cssCy, videoRect.width, videoRect.height, videoW, videoH);
    var rNative = edge.x - center.x;
    return { cx: center.x, cy: center.y, r: rNative, videoW: videoW, videoH: videoH };
  }

  // =========================================================================
  // Основной цикл распознавания кадров с камеры (throttled).
  // =========================================================================
  var SCAN_INTERVAL_MS = 380;
  var CRC_FAIL_THRESHOLD = 6; // ~2.3с подряд "распознано, но CRC не сошёлся"

  function startScanLoop() {
    if (state.loopTimer) clearInterval(state.loopTimer);
    state.loopTimer = setInterval(scanOneFrame, SCAN_INTERVAL_MS);
  }

  function scanOneFrame() {
    if (state.locked) return;
    var geo = getCrosshairVideoGeometry();
    if (!geo) return;

    // Кроп вокруг прицела (не весь кадр) — для производительности.
    var cropR = geo.r * 1.35;
    var x0 = Math.max(0, Math.floor(geo.cx - cropR));
    var y0 = Math.max(0, Math.floor(geo.cy - cropR));
    var x1 = Math.min(geo.videoW, Math.ceil(geo.cx + cropR));
    var y1 = Math.min(geo.videoH, Math.ceil(geo.cy + cropR));
    var w = x1 - x0, h = y1 - y0;
    if (w < 40 || h < 40) return;

    var canvas = els.captureCanvas;
    canvas.width = w;
    canvas.height = h;
    var ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(els.video, x0, y0, w, h, 0, 0, w, h);
    var imageData;
    try {
      imageData = ctx.getImageData(0, 0, w, h);
    } catch (e) {
      return; // тайнированный canvas или иная ошибка чтения — пропускаем кадр
    }

    var sampler = CMScanCore.makeSampler(imageData);
    var guessCx = geo.cx - x0, guessCy = geo.cy - y0;
    // Прицел откалиброван на внешний радиус метки (Parity-кольцо, r=420
    // design-unit) — см. cm-scan-core.js GEO.BORDER_OUT_R (внешняя рамка,
    // самый край видимой печатной метки).
    var guessS = geo.r / CMScanCore.GEO.BORDER_OUT_R;

    var result = CMScanCore.attemptDecode(sampler, guessCx, guessCy, guessS, {
      core: { centerSearchPx: guessS * 70, scaleRange: [0.75, 1.35], nSamples: 16, gridN: 5 },
    });

    handleFrameResult(result);
  }

  function handleFrameResult(result) {
    if (!result.ok) {
      state.consecutiveCrcFail = 0;
      setCameraStatus("Наведите CYRQODE в прицел");
      return;
    }
    if (result.payload.integrity_ok) {
      state.locked = true;
      clearInterval(state.loopTimer);
      state.loopTimer = null;
      stopCamera();
      showResultSuccess(result.payload);
      return;
    }
    state.consecutiveCrcFail++;
    setCameraStatus("Распознаю…");
    if (state.consecutiveCrcFail >= CRC_FAIL_THRESHOLD) {
      state.locked = true;
      clearInterval(state.loopTimer);
      state.loopTimer = null;
      stopCamera();
      showResultPartial();
    }
  }

  // =========================================================================
  // Разбор фото из файла (запасной путь, работает без живой камеры)
  // =========================================================================
  function decodeFromFile(file) {
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function () {
      URL.revokeObjectURL(url);
      var canvas = els.captureCanvas;
      var maxSide = 1400; // ограничение для производительности на телефоне
      var scaleDown = Math.min(1, maxSide / Math.max(img.width, img.height));
      var w = Math.round(img.width * scaleDown), h = Math.round(img.height * scaleDown);
      canvas.width = w;
      canvas.height = h;
      var ctx = canvas.getContext("2d");
      ctx.drawImage(img, 0, 0, w, h);
      var imageData;
      try {
        imageData = ctx.getImageData(0, 0, w, h);
      } catch (e) {
        showResultError("Не удалось прочитать изображение (" + e + ").");
        return;
      }
      var sampler = CMScanCore.makeSampler(imageData);
      // Фото не выровнено прицелом — предполагаем, что метка занимает
      // большую часть кадра (см. README генератора: ЦМ занимает
      // ориентировочно 80-85% кадра) и ищем шире, с большим бюджетом
      // выборок (это разовая операция, не 3 раза в секунду).
      var guessCx = w / 2, guessCy = h / 2;
      var guessS = (Math.min(w, h) * 0.42) / CMScanCore.GEO.BORDER_OUT_R;
      var result = CMScanCore.attemptDecode(sampler, guessCx, guessCy, guessS, {
        core: { centerSearchPx: Math.min(w, h) * 0.22, scaleRange: [0.55, 1.7], nSamples: 24, gridN: 7 },
      });
      if (!result.ok) {
        showResultError(
          "Не удалось найти ядро метки на фото (" + result.reason + "). " +
          "Убедитесь, что CYRQODE целиком попадает в кадр и хорошо освещена."
        );
        return;
      }
      if (result.payload.integrity_ok) {
        showResultSuccess(result.payload);
      } else {
        showResultPartial();
      }
    };
    img.onerror = function () {
      URL.revokeObjectURL(url);
      showResultError("Не удалось загрузить выбранный файл как изображение.");
    };
    img.src = url;
  }

  // =========================================================================
  // Экран результата
  // =========================================================================
  function clearResultActions() {
    els.resultActions.innerHTML = "";
  }
  function addAction(label, href, isPrimary) {
    var a = document.createElement("a");
    a.textContent = label;
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener";
    a.className = isPrimary ? "primary-btn" : "ghost-btn";
    els.resultActions.appendChild(a);
    return a;
  }

  function setResultField(container, rows) {
    container.innerHTML = "";
    rows.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "row";
      var k = document.createElement("span");
      k.className = "k";
      k.textContent = r[0];
      var v = document.createElement("span");
      v.className = "v";
      v.textContent = r[1];
      row.appendChild(k);
      row.appendChild(v);
      container.appendChild(row);
    });
    container.hidden = rows.length === 0;
  }

  function showResultSuccess(payload) {
    els.resultIcon.className = "status-icon ok";
    els.resultIcon.textContent = "✓";
    els.resultTitle.textContent = "CYRQODE распознан";
    els.resultText.textContent =
      "Контрольная сумма (CRC-16) сошлась. Идентификатор распознан; " +
      "запись в реестре может отсутствовать — это не проверялось.";
    setResultField(els.resultFields, [
      ["Entity ID", payload.entity_id],
      ["Запись", registryUrlFor(payload)],
      ["Namespace", payload.namespace],
      ["Версия профиля", "CM v" + payload.version + " (профиль " + payload.profile_id + ")"],
      ["Type hint", payload.type_hint],
    ]);
    clearResultActions();
    addAction("Открыть запись", registryUrlFor(payload), true);
    addAction("Сырые данные (JSON)", rawRecordUrlFor(payload), false);
    showScreen("result");
  }

  function showResultPartial() {
    els.resultIcon.className = "status-icon warn";
    els.resultIcon.textContent = "!";
    els.resultTitle.textContent = "CYRQODE распознан частично";
    els.resultText.textContent =
      "Контрольная сумма не сошлась. Возможные причины: CYRQODE не полностью " +
      "в прицеле, блики, недостаточный контраст печати или движение камеры. " +
      "Наведите CYRQODE точнее и попробуйте снова.";
    setResultField(els.resultFields, []);
    clearResultActions();
    showScreen("result");
  }

  function showResultError(text) {
    els.resultIcon.className = "status-icon warn";
    els.resultIcon.textContent = "!";
    els.resultTitle.textContent = "Не удалось распознать";
    els.resultText.textContent = text;
    setResultField(els.resultFields, []);
    clearResultActions();
    showScreen("result");
  }

  // =========================================================================
  // Навигация / обработчики
  // =========================================================================
  els.btnStartScan.addEventListener("click", startCameraFlow);

  els.btnCameraBack.addEventListener("click", function () {
    stopCamera();
    showScreen("start");
  });

  els.btnErrorBack.addEventListener("click", function () {
    showScreen("start");
  });

  els.btnResultBack.addEventListener("click", function () {
    state.locked = false;
    showScreen("start");
  });

  function openFilePicker() {
    els.fileInput.value = "";
    els.fileInput.click();
  }
  els.btnPickPhoto.addEventListener("click", openFilePicker);
  els.btnCameraFile.addEventListener("click", function () {
    stopCamera();
    openFilePicker();
  });
  els.btnErrorFile.addEventListener("click", openFilePicker);

  els.fileInput.addEventListener("change", function () {
    var file = els.fileInput.files && els.fileInput.files[0];
    if (!file) return;
    state.locked = true;
    decodeFromFile(file);
  });

  window.addEventListener("pagehide", stopCamera);
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stopCamera();
  });
})();
