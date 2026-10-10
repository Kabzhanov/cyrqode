/*!
 * scan.js — UI/камера для cm-scan (CYRQODE Circle Mark), v0.1.
 * Использует ТОЛЬКО cm-scan-core.js (тот же файл, что и selftest.html) для
 * распознавания геометрии — здесь только "склейка" с камерой/интерфейсом.
 * Никаких внешних библиотек.
 */
(function () {
  "use strict";

  var LANG_KEY = "bizdnaii_site_lang";
  var LANGS = ["en", "ru", "kz"];
  var TEXT = {
    en: {
      scan: "Scan", aim: "Point CYRQODE at the target", recognizing: "Reading…",
      cameraUnsupported: "This browser does not support camera access (getUserMedia is unavailable).",
      cameraDenied: "Camera access was denied or is unavailable (",
      cameraHint: "). This often happens when a messaging app opens the page in its built-in browser (Android WebView) without camera permission.",
      imageRead: "Could not read the image (",
      imageNotFound: "Could not find the mark core in the photo (",
      imageHint: "). Make sure the whole CYRQODE mark is in frame and well lit.",
      imageLoad: "Could not load the selected file as an image.",
      successTitle: "CYRQODE recognized",
      successText: "The CRC-16 checksum matches. The identifier was recognized; a registry record may not exist — this was not checked.",
      partialTitle: "CYRQODE partially recognized",
      partialText: "The checksum did not match. Possible causes: the mark is not fully in the target, glare, low print contrast, or camera movement. Aim more precisely and try again.",
      errorTitle: "Could not recognize",
      record: "Record", profile: "Profile version", openRecord: "Open record", rawData: "Raw data (JSON)"
    },
    ru: {
      scan: "Сканировать", aim: "Наведите CYRQODE в прицел", recognizing: "Распознаю…",
      cameraUnsupported: "Этот браузер/окно не поддерживает доступ к камере (getUserMedia недоступен).",
      cameraDenied: "Доступ к камере не выдан или недоступен (",
      cameraHint: "). Частый случай — приложение открыло страницу во встроенном просмотрщике (Android WebView) без разрешения на камеру.",
      imageRead: "Не удалось прочитать изображение (",
      imageNotFound: "Не удалось найти ядро метки на фото (",
      imageHint: "). Убедитесь, что CYRQODE целиком попадает в кадр и хорошо освещена.",
      imageLoad: "Не удалось загрузить выбранный файл как изображение.",
      successTitle: "CYRQODE распознан",
      successText: "Контрольная сумма (CRC-16) сошлась. Идентификатор распознан; запись в реестре может отсутствовать — это не проверялось.",
      partialTitle: "CYRQODE распознан частично",
      partialText: "Контрольная сумма не сошлась. Возможные причины: CYRQODE не полностью в прицеле, блики, недостаточный контраст печати или движение камеры. Наведите CYRQODE точнее и попробуйте снова.",
      errorTitle: "Не удалось распознать",
      record: "Запись", profile: "Версия профиля", openRecord: "Открыть запись", rawData: "Сырые данные (JSON)"
    },
    kz: {
      scan: "Сканерлеу", aim: "CYRQODE белгісін көздеу шеңберіне бағыттаңыз", recognizing: "Оқылуда…",
      cameraUnsupported: "Бұл браузер камераға қол жеткізуді қолдамайды (getUserMedia қолжетімсіз).",
      cameraDenied: "Камераға рұқсат берілмеді немесе камера қолжетімсіз (",
      cameraHint: "). Бұл мессенджер бетті камераға рұқсат бермейтін кіріктірілген браузерде (Android WebView) ашқанда жиі кездеседі.",
      imageRead: "Суретті оқу мүмкін болмады (",
      imageNotFound: "Фотодан белгі өзегін табу мүмкін болмады (",
      imageHint: "). CYRQODE белгісі кадрға толық сыйғанын және жарық жеткілікті екенін тексеріңіз.",
      imageLoad: "Таңдалған файлды сурет ретінде жүктеу мүмкін болмады.",
      successTitle: "CYRQODE танылды",
      successText: "CRC-16 бақылау сомасы сәйкес. Идентификатор танылды; тізілімдегі жазбаның бар-жоғы тексерілген жоқ.",
      partialTitle: "CYRQODE ішінара танылды",
      partialText: "Бақылау сомасы сәйкес келмеді. Себептері: белгі көздеу шеңберіне толық түспеген, жарық шағылысқан, баспа контрасты төмен немесе камера қозғалған болуы мүмкін. Дәлірек бағыттап, қайталап көріңіз.",
      errorTitle: "Тану мүмкін болмады",
      record: "Жазба", profile: "Профиль нұсқасы", openRecord: "Жазбаны ашу", rawData: "Шикі деректер (JSON)"
    }
  };
  var currentLang = "en";
  function t(key) { return TEXT[currentLang][key]; }
  function setLanguage(lang, persist) {
    currentLang = LANGS.indexOf(lang) !== -1 ? lang : "en";
    document.documentElement.lang = currentLang;
    document.documentElement.setAttribute("data-lang", currentLang);
    var toggle = document.getElementById("language-toggle");
    if (toggle) toggle.textContent = currentLang.toUpperCase();
    if (toggle) toggle.setAttribute("aria-label", currentLang === "en" ? "Change language" : currentLang === "ru" ? "Сменить язык" : "Тілді ауыстыру");
    var start = document.getElementById("btn-start-scan");
    if (start) start.setAttribute("aria-label", t("scan"));
    var back = document.getElementById("btn-camera-back");
    if (back) back.setAttribute("aria-label", currentLang === "en" ? "Back" : currentLang === "ru" ? "Назад" : "Артқа");
    document.title = currentLang === "en" ? "CYRQODE Circle Mark — Scanner (v0.1)" : currentLang === "ru" ? "CYRQODE Circle Mark — сканер (v0.1)" : "CYRQODE Circle Mark — сканер (0.1-нұсқа)";
    var description = document.querySelector('meta[name="description"]');
    if (description) description.content = currentLang === "en" ? "Scan a CYRQODE Circle Mark. Version 0.1." : currentLang === "ru" ? "Сканер физического знака Circle Mark (CYRQODE). Версия 0.1." : "CYRQODE Circle Mark белгісін сканерлеу. 0.1-нұсқа.";
    if (persist) {
      try {
        localStorage.setItem(LANG_KEY, currentLang);
        localStorage.setItem("bizdnaii_widget_lang", currentLang);
      } catch (e) {}
      try { window.dispatchEvent(new CustomEvent("bizdnaii-language-change", { detail: { language: currentLang } })); } catch (e) {}
    }
  }
  try { setLanguage(localStorage.getItem(LANG_KEY) || document.documentElement.getAttribute("data-lang") || "en", false); }
  catch (e) { setLanguage(document.documentElement.getAttribute("data-lang") || "en", false); }
  var languageToggle = document.getElementById("language-toggle");
  if (languageToggle) languageToggle.addEventListener("click", function () {
    setLanguage(LANGS[(LANGS.indexOf(currentLang) + 1) % LANGS.length], true);
  });

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
        t("cameraUnsupported")
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
        setCameraStatus(t("aim"));
        els.video.play().catch(function () {});
        els.video.onloadedmetadata = function () {
          startScanLoop();
        };
      })
      .catch(function (err) {
        var msg =
          t("cameraDenied") +
          (err && err.name ? err.name : String(err)) +
          t("cameraHint");
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
      setCameraStatus(t("aim"));
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
    setCameraStatus(t("recognizing"));
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
        showResultError(t("imageRead") + e + ").");
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
          t("imageNotFound") + result.reason + t("imageHint")
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
      showResultError(t("imageLoad"));
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
    els.resultTitle.textContent = t("successTitle");
    els.resultText.textContent = t("successText");
    setResultField(els.resultFields, [
      ["Entity ID", payload.entity_id],
      [t("record"), registryUrlFor(payload)],
      ["Namespace", payload.namespace],
      [t("profile"), "CM v" + payload.version + " (" + (currentLang === "en" ? "profile " : currentLang === "ru" ? "профиль " : "профиль ") + payload.profile_id + ")"],
      ["Type hint", payload.type_hint],
    ]);
    clearResultActions();
    addAction(t("openRecord"), registryUrlFor(payload), true);
    addAction(t("rawData"), rawRecordUrlFor(payload), false);
    showScreen("result");
  }

  function showResultPartial() {
    els.resultIcon.className = "status-icon warn";
    els.resultIcon.textContent = "!";
    els.resultTitle.textContent = t("partialTitle");
    els.resultText.textContent = t("partialText");
    setResultField(els.resultFields, []);
    clearResultActions();
    showScreen("result");
  }

  function showResultError(text) {
    els.resultIcon.className = "status-icon warn";
    els.resultIcon.textContent = "!";
    els.resultTitle.textContent = t("errorTitle");
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
