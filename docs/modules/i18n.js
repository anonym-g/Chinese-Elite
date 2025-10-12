// docs/modules/i18n.js

let translations = {};
let currentLang = 'zh-cn';

/**
 * 加载指定语言的翻译文件
 * @param {string} lang - 'en' or 'zh-cn'
 */
export async function loadTranslations(lang) {
  try {
    const response = await fetch(`./locales/${lang}.json`);
    if (!response.ok) throw new Error('Network response was not ok');
    translations = await response.json();
    currentLang = lang;
    document.documentElement.lang = lang === 'zh-cn' ? 'zh-cn' : 'en';
  } catch (error) {
    console.error(`Could not load ${lang} translations, falling back to English:`, error);
    if (lang !== 'en') {
        await loadTranslations('en');
    }
  }
}

/**
 * 获取翻译文本
 * @param {string} key - The key from the JSON file
 * @param {object} [replaces={}] - An optional object of placeholders to replace
 * @returns {string} - The translated text
 */
export function t(key, replaces = {}) {
  let text = translations[key] || key;
  for(const [placeholder, value] of Object.entries(replaces)) {
      text = text.replace(`{${placeholder}}`, value);
  }
  return text;
}

/**
 * 获取当前语言
 * @returns {string}
 */
export function getCurrentLanguage() {
    return currentLang;
}
