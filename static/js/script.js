(function () {
    const sourceTextArea = document.getElementById("source-text");
    const translateAllButton = document.getElementById("translate-all-btn");
    const clearButton = document.getElementById("clear-btn");
    const resultsContainer = document.getElementById("results-container");
    const sourceLanguageInfo = document.getElementById("source-language-info");
    const languageCheckboxes = document.getElementById("language-checkboxes");
    const selectAllBtn = document.getElementById("select-all-btn");
    const deselectAllBtn = document.getElementById("deselect-all-btn");
    const toggleMobileLanguagesBtn = document.getElementById("toggle-mobile-languages-btn");
    const customLanguageSelect = document.getElementById("custom-language-select");
    const charCountEl = document.getElementById("char-count");
    const maxCharCountEl = document.getElementById("max-char-count");
    const selectedCountEl = document.getElementById("selected-count");
    const selectedHiddenCountEl = document.getElementById("selected-hidden-count");
    const emptyState = document.getElementById("empty-state");
    const toastContainer = document.getElementById("toast-container");
    const settingsButton = document.getElementById("settings-btn");
    const settingsModal = document.getElementById("settings-modal");
    const settingsForm = document.getElementById("settings-form");
    const settingsCloseButton = document.getElementById("settings-close-btn");
    const settingsResetButton = document.getElementById("settings-reset-btn");
    const settingsTestButton = document.getElementById("settings-test-btn");
    const connectionStatus = document.getElementById("api-connection-status");
    const effectiveConfig = document.getElementById("api-effective-config");
    const effectiveList = document.getElementById("api-effective-list");
    const effectiveNote = document.getElementById("api-effective-note");
    const settingsPanel = settingsModal.querySelector(".settings-panel");
    const apiBaseUrlInput = document.getElementById("api-base-url");
    const apiKeyInput = document.getElementById("api-key");
    const apiModelInput = document.getElementById("api-model");
    const apiProviderSelect = document.getElementById("api-provider");
    const openaiProviderFields = document.getElementById("openai-provider-fields");
    const indexProviderHint = document.getElementById("index-provider-hint");
    const PROVIDER_LABELS = { openai: "OpenAI 兼容 API", index: "Index-Translate" };
    const CUSTOM_LANGUAGE_KEY = "selectedCustomLanguage";
    const API_SETTINGS_KEY = "apiSettings";
    const API_KEY_SESSION_KEY = "apiKey";
    const storageMemory = { localStorage: new Map(), sessionStorage: new Map() };

    let availableLanguages = {};
    let defaultOrder = [];
    let customOptions = {};
    let serverDefaultProvider = "openai";
    let mobileLanguagesExpanded = false;
    let maxTextLength = Number(sourceTextArea.dataset.maxTextLength) || 5000;
    let translationController = null;
    let translationVersion = 0;
    let connectionController = null;
    let connectionVersion = 0;
    let settingsReturnFocus = null;
    let modalBackgroundState = [];
    let previousBodyOverflow = "";

    const MOBILE_VISIBLE_LANGUAGE_COUNT = 4;

    init();

    async function init() {
        migrateApiSettings();
        bindEvents();
        updateCharCount();
        await loadLanguages();
        autoResizeTextarea();
    }

    function bindEvents() {
        clearButton.addEventListener("click", () => {
            cancelTranslation();
            sourceTextArea.value = "";
            updateCharCount();
            autoResizeTextarea();
            clearResults();
            sourceTextArea.focus();
            showNotification("文本已清空", "success");
        });

        selectAllBtn.addEventListener("click", () => {
            cancelTranslation();
            const checkboxes = languageCheckboxes.querySelectorAll('input[type="checkbox"]');
            checkboxes.forEach((checkbox) => {
                checkbox.checked = true;
                updateCheckboxStyle(checkbox.closest(".language-checkbox-item"));
            });
            syncCustomLanguageState();
            persistSelectedLanguages();
            updateSelectedCount();
        });

        deselectAllBtn.addEventListener("click", () => {
            cancelTranslation();
            const checkboxes = languageCheckboxes.querySelectorAll('input[type="checkbox"]');
            checkboxes.forEach((checkbox) => {
                checkbox.checked = false;
                updateCheckboxStyle(checkbox.closest(".language-checkbox-item"));
            });
            syncCustomLanguageState();
            persistSelectedLanguages();
            updateSelectedCount();
        });

        if (toggleMobileLanguagesBtn) {
            toggleMobileLanguagesBtn.addEventListener("click", () => {
                mobileLanguagesExpanded = !mobileLanguagesExpanded;
                updateMobileLanguageVisibility();
            });
        }

        if (customLanguageSelect) {
            customLanguageSelect.addEventListener("change", () => {
                cancelTranslation();
                persistSelectedCustomLanguage();
            });
        }

        translateAllButton.addEventListener("click", translateSelected);

        if (settingsButton && settingsModal && settingsForm) {
            settingsButton.addEventListener("click", openSettings);
            settingsCloseButton.addEventListener("click", closeSettings);
            settingsModal.addEventListener("click", (event) => {
                if (event.target.hasAttribute("data-close-settings")) {
                    closeSettings();
                }
            });
            settingsForm.addEventListener("submit", (event) => {
                event.preventDefault();
                saveApiSettings();
            });
            settingsResetButton.addEventListener("click", clearApiSettings);
            settingsTestButton.addEventListener("click", testApiConnection);
            [apiBaseUrlInput, apiKeyInput, apiModelInput].forEach((input) => {
                input.addEventListener("input", cancelConnectionTest);
            });
            apiProviderSelect.addEventListener("change", () => {
                cancelConnectionTest();
                updateProviderFields();
            });
        }

        sourceTextArea.addEventListener("input", () => {
            cancelTranslation();
            updateCharCount();
            autoResizeTextarea();
        });

        sourceTextArea.addEventListener("keydown", (event) => {
            if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                translateAllButton.click();
            }
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && settingsModal && !settingsModal.hidden) {
                event.preventDefault();
                closeSettings();
            } else if (event.key === "Tab" && !settingsModal.hidden) {
                trapSettingsFocus(event);
            }
        });
        document.addEventListener("focusin", (event) => {
            if (!settingsModal.hidden && !settingsPanel.contains(event.target)) {
                apiProviderSelect.focus();
            }
        });
    }

    async function loadLanguages() {
        try {
            const response = await fetch("/languages");
            const payload = await readResponse(response);
            if (!response.ok) {
                throw new Error(payload.error || "获取语言列表失败");
            }

            availableLanguages = payload.languages || {};
            defaultOrder = payload.default_order || Object.keys(availableLanguages);
            customOptions = payload.custom_options || {};
            if (Object.prototype.hasOwnProperty.call(PROVIDER_LABELS, payload.default_provider)) {
                serverDefaultProvider = payload.default_provider;
                updateProviderFields();
            }
            if (Number.isInteger(payload.max_text_length) && payload.max_text_length > 0) {
                maxTextLength = payload.max_text_length;
            }
            updateCharCount();

            renderLanguageCheckboxes();
            renderCustomLanguageSelect();
            restoreSelectedLanguages();
            updateSelectedCount();
        } catch (error) {
            console.error(error);
            showNotification("加载语言列表失败", "error");
        }
    }

    function renderLanguageCheckboxes() {
        languageCheckboxes.innerHTML = "";

        defaultOrder.forEach((langCode) => {
            if (Object.prototype.hasOwnProperty.call(customOptions, langCode)) {
                return;
            }
            const langName = availableLanguages[langCode];
            if (!langName) return;

            languageCheckboxes.appendChild(createLanguageItem(langCode, langName, true, false));
        });

        if (Object.keys(customOptions).length) {
            languageCheckboxes.appendChild(createLanguageItem("custom", "自定义语言", false, true));
        }
        updateMobileLanguageVisibility();
    }

    function createLanguageItem(langCode, langName, checked, pinned) {
        // Wrapping the checkbox in a label gives text and blank space one native toggle.
        const item = document.createElement("label");
        item.className = "language-checkbox-item";
        item.dataset.mobilePinned = String(pinned);
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.id = `lang-${langCode}`;
        checkbox.value = langCode;
        checkbox.checked = checked;
        const name = document.createElement("span");
        name.className = "language-label";
        name.textContent = langName;
        item.append(checkbox, name);
        updateCheckboxStyle(item);
        checkbox.addEventListener("change", () => {
            cancelTranslation();
            updateCheckboxStyle(item);
            syncCustomLanguageState();
            persistSelectedLanguages();
            updateSelectedCount();
        });
        return item;
    }

    function renderCustomLanguageSelect() {
        if (!customLanguageSelect) return;
        const currentValue = customLanguageSelect.value;
        customLanguageSelect.innerHTML = "";

        const optionEntries = Object.entries(customOptions);

        optionEntries.forEach(([code, name]) => {
            const option = document.createElement("option");
            option.value = code;
            option.textContent = name;
            customLanguageSelect.appendChild(option);
        });

        const savedCustomLanguage = readStorage("localStorage", CUSTOM_LANGUAGE_KEY);
        const candidateValue = savedCustomLanguage || currentValue;
        const canRestore = candidateValue && optionEntries.some(([code]) => code === candidateValue);
        customLanguageSelect.value = canRestore ? candidateValue : (optionEntries[0] ? optionEntries[0][0] : "");
        syncCustomLanguageState();
    }

    function updateCheckboxStyle(item) {
        if (!item) return;
        const checkbox = item.querySelector('input[type="checkbox"]');
        if (checkbox && checkbox.checked) {
            item.classList.add("selected");
            return;
        }
        item.classList.remove("selected");
    }

    function updateMobileLanguageVisibility() {
        if (!toggleMobileLanguagesBtn) return;

        const isMobile = window.matchMedia("(max-width: 768px)").matches;
        const items = Array.from(languageCheckboxes.querySelectorAll(".language-checkbox-item"));
        const regularItems = items.filter((item) => item.dataset.mobilePinned !== "true");

        if (!isMobile) {
            items.forEach((item) => {
                item.hidden = false;
            });
            toggleMobileLanguagesBtn.hidden = true;
            mobileLanguagesExpanded = false;
            toggleMobileLanguagesBtn.setAttribute("aria-expanded", "false");
            updateSelectedCount();
            return;
        }

        toggleMobileLanguagesBtn.hidden = regularItems.length <= MOBILE_VISIBLE_LANGUAGE_COUNT;

        regularItems.forEach((item, index) => {
            item.hidden = !mobileLanguagesExpanded && index >= MOBILE_VISIBLE_LANGUAGE_COUNT;
        });

        toggleMobileLanguagesBtn.textContent = mobileLanguagesExpanded ? "收起语言" : "更多语言";
        toggleMobileLanguagesBtn.setAttribute("aria-expanded", String(mobileLanguagesExpanded));
        updateSelectedCount();
    }

    function getSelectedLanguages() {
        const checked = languageCheckboxes.querySelectorAll('input[type="checkbox"]:checked');
        return Array.from(checked).map((node) => node.value);
    }

    function persistSelectedLanguages() {
        const selected = getSelectedLanguages();
        writeStorage("localStorage", "selectedLanguages", JSON.stringify(selected));
    }

    function persistSelectedCustomLanguage() {
        if (!customLanguageSelect) return;
        writeStorage("localStorage", CUSTOM_LANGUAGE_KEY, customLanguageSelect.value);
    }

    function restoreSelectedLanguages() {
        const raw = readStorage("localStorage", "selectedLanguages");
        if (!raw) return;

        try {
            const stored = JSON.parse(raw);
            if (!Array.isArray(stored)) return;
            const selected = new Set(stored);
            const checkboxes = languageCheckboxes.querySelectorAll('input[type="checkbox"]');
            checkboxes.forEach((checkbox) => {
                checkbox.checked = selected.has(checkbox.value);
                updateCheckboxStyle(checkbox.closest(".language-checkbox-item"));
            });
            syncCustomLanguageState();
        } catch (error) {
            console.error(error);
        }
    }

    function isCustomLanguageChecked() {
        const customCheckbox = languageCheckboxes.querySelector('#lang-custom');
        return !!(customCheckbox && customCheckbox.checked);
    }

    function syncCustomLanguageState() {
        if (!customLanguageSelect) return;
        const customRow = customLanguageSelect.closest(".custom-language-row");
        const enabled = isCustomLanguageChecked();
        customLanguageSelect.disabled = !enabled;
        if (customRow) {
            customRow.hidden = !enabled;
        }
    }

    async function translateSelected() {
        const text = sourceTextArea.value.trim();
        const selectedLanguages = getSelectedLanguages();
        const selectedCustomLanguage = isCustomLanguageChecked() && customLanguageSelect ? customLanguageSelect.value : "";

        if (!text) {
            showNotification("请输入要翻译的文本", "error");
            sourceTextArea.focus();
            return;
        }

        if (Array.from(text).length > maxTextLength) {
            showNotification(`文本长度不能超过 ${maxTextLength} 字符`, "error");
            return;
        }

        if (selectedLanguages.length === 0) {
            showNotification("请至少选择一种目标语言", "error");
            return;
        }

        cancelTranslation();
        const requestVersion = translationVersion;
        const controller = new AbortController();
        translationController = controller;
        setButtonLoading(true);

        try {
            const response = await fetch("/translate_all", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                signal: controller.signal,
                body: JSON.stringify({
                    text,
                    languages: selectedLanguages,
                    custom_language: selectedCustomLanguage,
                    api_settings: getApiSettings()
                })
            });

            const payload = await readResponse(response);
            if (requestVersion !== translationVersion) return;
            const results = Array.isArray(payload.results) ? payload.results : [];
            const successCount = results.filter((result) => result.ok === true).length;
            const failedCount = results.length - successCount;
            if (!results.length) {
                throw new Error(payload.error || (response.ok ? "服务未返回翻译结果" : "翻译失败"));
            }
            const keptPreviousResults = !successCount && resultsContainer.children.length > 0;
            if (!keptPreviousResults) {
                displayTranslationResults({ ...payload, results });
            }
            if (!successCount) {
                const retained = keptPreviousResults ? "；已保留上次结果" : "";
                showNotification(`${payload.error || `全部 ${failedCount} 种语言翻译失败，请重试`}${retained}`, "error");
            } else if (failedCount) {
                showNotification(`已完成 ${successCount} 项，${failedCount} 项失败，详见结果卡片`, "error");
            } else {
                showNotification(`翻译完成，生成 ${successCount} 项结果`, "success");
            }
        } catch (error) {
            if (requestVersion !== translationVersion || error.name === "AbortError") return;
            const retained = resultsContainer.children.length ? "；已保留上次结果" : "";
            showNotification(`${error.message || "翻译失败"}${retained}`, "error");
        } finally {
            if (requestVersion === translationVersion) {
                translationController = null;
                setButtonLoading(false);
            }
        }
    }

    function cancelTranslation() {
        translationVersion += 1;
        if (translationController) translationController.abort();
        translationController = null;
        setButtonLoading(false);
    }

    async function readResponse(response) {
        try {
            const payload = await response.json();
            if (payload && typeof payload === "object" && !Array.isArray(payload)) return payload;
        } catch (_) {
            // Handle proxy HTML and empty responses without exposing response content.
        }
        throw new Error("服务返回了无效响应，请重试");
    }

    function setButtonLoading(loading) {
        translateAllButton.disabled = loading;
        translateAllButton.classList.toggle("is-loading", loading);
        translateAllButton.setAttribute("aria-busy", String(loading));
    }

    function displayTranslationResults(payload) {
        const sourceKnown = payload.source_language && payload.source_language !== "unknown";
        // An unconfirmed hint is still translated, so it must not read as a detection.
        const sourceLabel = payload.source_confirmed === false ? "推测源语言" : "检测到源语言";
        sourceLanguageInfo.textContent = sourceKnown
            ? `${sourceLabel}：${getLanguageName(payload.source_language)}` : "源语言：未能确定";
        sourceLanguageInfo.hidden = false;

        resultsContainer.innerHTML = "";

        payload.results.forEach((result) => {
            const successful = result.ok === true;
            const languageName = result.language_name || getLanguageName(result.language);
            const card = document.createElement("article");
            card.className = successful ? "language-result" : "language-result is-error";

            const header = document.createElement("div");
            header.className = "language-header";

            const name = document.createElement("span");
            name.className = "language-name";
            name.textContent = languageName;
            header.appendChild(name);

            const resultActions = document.createElement("div");
            resultActions.className = "result-actions";

            if (successful && result.is_original) {
                const badge = document.createElement("span");
                badge.className = "original-badge";
                badge.textContent = "原文";
                resultActions.appendChild(badge);
            }

            if (successful) {
                const copyButton = document.createElement("button");
                copyButton.type = "button";
                copyButton.className = "copy-result-button";
                copyButton.textContent = "复制";
                copyButton.title = `复制${languageName}翻译结果`;
                copyButton.addEventListener("click", () => copyResultText(result.translated_text, languageName));
                resultActions.appendChild(copyButton);
            } else {
                const badge = document.createElement("span");
                badge.className = "failure-badge";
                badge.textContent = "失败";
                resultActions.appendChild(badge);
            }

            header.appendChild(resultActions);

            const text = document.createElement("div");
            text.className = successful ? "language-text" : "language-text result-error";
            text.textContent = successful ? result.translated_text : (result.error || "翻译失败，请重试");

            if (successful) {
                text.addEventListener("dblclick", () => copyResultText(result.translated_text, languageName));
                text.addEventListener("contextmenu", (event) => {
                    event.preventDefault();
                    copyResultText(result.translated_text, languageName);
                });
            }

            card.appendChild(header);
            card.appendChild(text);
            resultsContainer.appendChild(card);
        });

        emptyState.style.display = payload.results.length ? "none" : "block";
    }

    async function copyResultText(text, languageName) {
        try {
            await copyToClipboard(text);
            showNotification(`${languageName} 已复制`, "success");
        } catch (error) {
            console.error(error);
            showNotification("复制失败，请手动选择文本复制", "error");
        }
    }

    function clearResults() {
        resultsContainer.innerHTML = "";
        sourceLanguageInfo.textContent = "";
        sourceLanguageInfo.hidden = true;
        emptyState.style.display = "block";
    }

    function getLanguageName(code) {
        return availableLanguages[code] || customOptions[code] || code || "未知语言";
    }

    function updateCharCount() {
        const value = sourceTextArea.value || "";
        charCountEl.textContent = String(Array.from(value).length);
        if (maxCharCountEl) maxCharCountEl.textContent = String(maxTextLength);
    }

    function updateSelectedCount() {
        selectedCountEl.textContent = String(getSelectedLanguages().length);
        const hiddenSelected = languageCheckboxes.querySelectorAll('.language-checkbox-item[hidden] input:checked').length;
        if (selectedHiddenCountEl) {
            selectedHiddenCountEl.textContent = hiddenSelected ? `（其中 ${hiddenSelected} 种在收起区域）` : "";
        }
    }

    function autoResizeTextarea() {
        sourceTextArea.style.height = "auto";
        sourceTextArea.style.height = `${sourceTextArea.scrollHeight}px`;
    }

    function openSettings() {
        if (!settingsModal.hidden) return;
        const settings = getApiSettings();
        apiBaseUrlInput.value = settings.base_url || "";
        apiKeyInput.value = settings.api_key || "";
        apiModelInput.value = settings.model || "";
        apiProviderSelect.value = settings.provider;
        updateProviderFields();
        cancelConnectionTest();
        settingsReturnFocus = document.activeElement;
        previousBodyOverflow = document.body.style.overflow;
        modalBackgroundState = Array.from(document.body.children)
            .filter((element) => element !== settingsModal && element !== toastContainer && element.tagName !== "SCRIPT")
            .map((element) => ({ element, inert: element.inert }));
        modalBackgroundState.forEach(({ element }) => { element.inert = true; });
        document.body.style.overflow = "hidden";
        settingsModal.hidden = false;
        apiProviderSelect.focus();
    }

    function updateProviderFields() {
        const isIndex = (apiProviderSelect.value || serverDefaultProvider) === "index";
        openaiProviderFields.hidden = isIndex;
        // Disabled fields skip form validation, so a hidden invalid URL cannot block saving.
        apiBaseUrlInput.disabled = isIndex;
        apiKeyInput.disabled = isIndex;
        indexProviderHint.hidden = !isIndex;
        apiModelInput.placeholder = isIndex ? "Index-Translate-35B-A3B" : "gpt-3.5-turbo";
    }

    function closeSettings() {
        if (settingsModal.hidden) return;
        cancelConnectionTest();
        settingsModal.hidden = true;
        modalBackgroundState.forEach(({ element, inert }) => { element.inert = inert; });
        modalBackgroundState = [];
        document.body.style.overflow = previousBodyOverflow;
        const focusTarget = settingsReturnFocus && settingsReturnFocus.isConnected ? settingsReturnFocus : settingsButton;
        focusTarget.focus({ preventScroll: true });
        settingsReturnFocus = null;
    }

    function trapSettingsFocus(event) {
        const focusable = Array.from(settingsPanel.querySelectorAll('button, input, select, textarea, a[href], [tabindex]'))
            .filter((element) => !element.disabled && element.tabIndex >= 0 && !element.hidden && element.getClientRects().length);
        if (!focusable.length) {
            event.preventDefault();
            settingsPanel.focus();
            return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;
        if (event.shiftKey && (active === first || !focusable.includes(active))) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && (active === last || !focusable.includes(active))) {
            event.preventDefault();
            first.focus();
        }
    }

    // A blocked browser storage policy still allows settings to work in memory.
    function readStorage(area, key) {
        const memory = storageMemory[area];
        if (memory.has(key)) return memory.get(key);
        let value = null;
        try {
            value = window[area].getItem(key);
        } catch (_) {
            // Storage can be unavailable in private mode or embedded browsers.
        }
        memory.set(key, value);
        return value;
    }

    function writeStorage(area, key, value) {
        storageMemory[area].set(key, value);
        try {
            if (value === null) window[area].removeItem(key);
            else window[area].setItem(key, value);
        } catch (_) {
            // The in-memory copy remains usable for the current page.
        }
    }

    function readStoredObject(area, key) {
        try {
            const parsed = JSON.parse(readStorage(area, key));
            return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
        } catch (_) {
            return {};
        }
    }

    function migrateApiSettings() {
        const saved = readStoredObject("localStorage", API_SETTINGS_KEY);
        if (typeof saved.api_key === "string" && saved.api_key && !readStorage("sessionStorage", API_KEY_SESSION_KEY)) {
            writeStorage("sessionStorage", API_KEY_SESSION_KEY, saved.api_key);
        }
        if (Object.prototype.hasOwnProperty.call(saved, "api_key")) {
            writeStorage("localStorage", API_SETTINGS_KEY, JSON.stringify({
                base_url: typeof saved.base_url === "string" ? saved.base_url : "",
                model: typeof saved.model === "string" ? saved.model : "",
                provider: storedProvider(saved)
            }));
        }
    }

    function storedProvider(saved) {
        return Object.prototype.hasOwnProperty.call(PROVIDER_LABELS, saved.provider) ? saved.provider : "";
    }

    function getApiSettings() {
        const saved = readStoredObject("localStorage", API_SETTINGS_KEY);
        return {
            base_url: typeof saved.base_url === "string" ? saved.base_url : "",
            api_key: readStorage("sessionStorage", API_KEY_SESSION_KEY) || "",
            model: typeof saved.model === "string" ? saved.model : "",
            provider: storedProvider(saved)
        };
    }

    function getDraftApiSettings() {
        return {
            base_url: apiBaseUrlInput.value.trim().replace(/\/+$/, ""),
            api_key: apiKeyInput.value.trim(),
            model: apiModelInput.value.trim(),
            provider: apiProviderSelect.value
        };
    }

    function saveApiSettings() {
        const settings = getDraftApiSettings();
        cancelTranslation();
        writeStorage("localStorage", API_SETTINGS_KEY, JSON.stringify({
            base_url: settings.base_url, model: settings.model, provider: settings.provider
        }));
        writeStorage("sessionStorage", API_KEY_SESSION_KEY, settings.api_key || null);
        closeSettings();
        showNotification("API 设置已保存", "success");
    }

    function clearApiSettings() {
        cancelTranslation();
        cancelConnectionTest();
        writeStorage("localStorage", API_SETTINGS_KEY, null);
        writeStorage("sessionStorage", API_KEY_SESSION_KEY, null);
        apiBaseUrlInput.value = "";
        apiKeyInput.value = "";
        apiModelInput.value = "";
        apiProviderSelect.value = "";
        updateProviderFields();
        showNotification("API 设置已清除", "success");
    }

    function cancelConnectionTest() {
        connectionVersion += 1;
        if (connectionController) connectionController.abort();
        connectionController = null;
        setConnectionLoading(false);
        connectionStatus.hidden = true;
        connectionStatus.textContent = "";
        connectionStatus.className = "api-connection-status";
        hideEffectiveConfig();
    }

    function hideEffectiveConfig() {
        effectiveConfig.hidden = true;
        effectiveList.replaceChildren();
        effectiveNote.hidden = true;
        effectiveNote.textContent = "";
    }

    function showEffectiveConfig(effective) {
        if (!effective || typeof effective !== "object") return hideEffectiveConfig();
        const sourceLabel = (source) => (source === "server" ? "服务器配置" : "页面设置");
        const rows = [
            ["服务", PROVIDER_LABELS[effective.provider], ""],
            ["Base URL", effective.base_url, sourceLabel(effective.base_url_source)],
            ["Key", effective.api_key_hint, sourceLabel(effective.key_source)],
            ["模型", effective.model, sourceLabel(effective.model_source)]
        ];
        const nodes = [];
        for (const [label, value, source] of rows) {
            if (!value) continue;
            const term = document.createElement("dt");
            term.textContent = label;
            const detail = document.createElement("dd");
            // textContent only: these strings come from the server configuration.
            detail.textContent = value;
            if (source) {
                const origin = document.createElement("span");
                origin.className = "api-effective-source";
                origin.textContent = source;
                detail.appendChild(origin);
            }
            nodes.push(term, detail);
        }
        effectiveList.replaceChildren(...nodes);
        if (effective.fell_back_to_server) {
            effectiveNote.textContent =
                "页面填写的 Base URL 没有配套的 Key，已连同页面模型名一起忽略，改用服务器配置。";
            effectiveNote.hidden = false;
        } else {
            effectiveNote.hidden = true;
            effectiveNote.textContent = "";
        }
        effectiveConfig.hidden = nodes.length === 0 && effectiveNote.hidden;
    }

    function setConnectionLoading(loading) {
        settingsTestButton.disabled = loading;
        settingsTestButton.textContent = loading ? "检测中…" : "检测连接";
        settingsTestButton.setAttribute("aria-busy", String(loading));
    }

    function showConnectionStatus(message, state) {
        connectionStatus.className = `api-connection-status ${state}`;
        connectionStatus.textContent = message;
        connectionStatus.hidden = false;
    }

    async function testApiConnection() {
        if (connectionController) return;
        if (!settingsForm.reportValidity()) return;
        cancelConnectionTest();
        const requestVersion = connectionVersion;
        const controller = new AbortController();
        connectionController = controller;
        setConnectionLoading(true);
        showConnectionStatus("正在检测当前设置，请稍候…", "pending");
        try {
            const response = await fetch("/test_connection", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                signal: controller.signal,
                body: JSON.stringify({ api_settings: getDraftApiSettings() })
            });
            const payload = await readResponse(response);
            if (requestVersion !== connectionVersion) return;
            // Read the effective configuration before any throw: a rejected
            // credential is exactly when the user needs to see what was sent.
            const effective = payload && payload.effective;
            if (!response.ok || payload.ok !== true) {
                const failure = new Error(payload.error || "API 连接检测失败");
                failure.effective = effective;
                throw failure;
            }
            const details = [];
            if (typeof payload.latency_ms === "number" && Number.isFinite(payload.latency_ms)) {
                details.push(`${Math.round(payload.latency_ms)} ms`);
            }
            if (payload.model) details.push(`模型：${payload.model}`);
            showConnectionStatus(`${payload.message || "连接成功"}${details.length ? `（${details.join("，")}）` : ""}`, "success");
            showEffectiveConfig(effective);
        } catch (error) {
            if (requestVersion !== connectionVersion || error.name === "AbortError") return;
            showConnectionStatus(error.message || "API 连接检测失败", "error");
            showEffectiveConfig(error.effective);
        } finally {
            if (requestVersion === connectionVersion) {
                connectionController = null;
                setConnectionLoading(false);
            }
        }
    }

    window.addEventListener("resize", updateMobileLanguageVisibility);

    async function copyToClipboard(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            try {
                await navigator.clipboard.writeText(text);
                return;
            } catch (_) {
                // Permission or secure-context failures can still use the fallback.
            }
        }
        fallbackCopyText(text);
    }

    function fallbackCopyText(text) {
        const previousFocus = document.activeElement;
        const textArea = document.createElement("textarea");
        textArea.value = text;
        textArea.setAttribute("readonly", "");
        textArea.style.position = "fixed";
        textArea.style.opacity = "0";
        document.body.appendChild(textArea);
        textArea.style.pointerEvents = "none";
        textArea.style.top = "0";
        try {
            textArea.focus({ preventScroll: true });
            textArea.select();
            if (!document.execCommand("copy")) throw new Error("复制命令失败");
        } finally {
            textArea.remove();
            if (previousFocus && previousFocus.isConnected) previousFocus.focus({ preventScroll: true });
        }
    }

    function showNotification(message, type) {
        const toast = document.createElement("div");
        toast.className = `toast ${type}`;
        toast.textContent = message;
        toastContainer.appendChild(toast);

        window.setTimeout(() => {
            toast.remove();
        }, 2600);
    }
})();
