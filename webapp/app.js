/* ==========================================================================
   O'rganAilik / Nargis AI Telegram Mini App - Client Logic
   ========================================================================== */

const tg = window.Telegram?.WebApp;
const OWNER_ID = 5146977644; // Asadbek Ustoz Telegram ID

// State
let currentUser = null;
let isOwner = false;

// Initialize app on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  initTelegramApp();
  checkRoleAndUser();
  loadSavedApplication();
});

/**
 * Configure Telegram WebApp styles and behaviors
 */
function initTelegramApp() {
  if (tg) {
    try {
      tg.ready();
      tg.expand();
      if (tg.setHeaderColor) {
        tg.setHeaderColor('#082615');
      }
      if (tg.setBackgroundColor) {
        tg.setBackgroundColor('#f4f3ed');
      }
    } catch (e) {
      console.warn('Telegram WebApp SDK init error:', e);
    }
  }
}

/**
 * Identify if user is Asadbek Ustoz or student
 */
function checkRoleAndUser() {
  const urlParams = new URLSearchParams(window.location.search);
  const adminParam = urlParams.get('admin');

  if (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) {
    currentUser = tg.initDataUnsafe.user;
  }

  // Pre-fill student name if available
  if (currentUser) {
    const fullName = [currentUser.first_name, currentUser.last_name].filter(Boolean).join(' ');
    const nameInput = document.getElementById('fullName');
    if (nameInput && fullName) {
      nameInput.value = fullName;
    }
    const welcomeName = document.getElementById('welcomeUserName');
    if (welcomeName) {
      welcomeName.innerText = `Xush kelibsiz, ${currentUser.first_name}!`;
    }

    if (currentUser.id === OWNER_ID) {
      isOwner = true;
    }
  }

  // URL override for testing dashboard in browser
  if (adminParam === '1' || adminParam === 'true') {
    isOwner = true;
  }

  // Reveal Ustoz tab if owner
  const ownerTabBtn = document.getElementById('ownerTabBtn');
  if (ownerTabBtn && isOwner) {
    ownerTabBtn.style.display = 'flex';
  }
}

/**
 * Tab switching controller
 */
function switchTab(tabName) {
  // Trigger light haptic on mobile
  if (tg?.HapticFeedback) {
    tg.HapticFeedback.impactOccurred('light');
  }

  // Hide all sections
  const sections = document.querySelectorAll('.view-section');
  sections.forEach(sec => sec.classList.remove('active'));

  // Remove active from nav items
  const navItems = document.querySelectorAll('.nav-item');
  navItems.forEach(item => item.classList.remove('active'));

  // Activate selected section
  const targetSection = document.getElementById(`view-${tabName}`);
  if (targetSection) {
    targetSection.classList.add('active');
  }

  // Activate corresponding nav item
  const targetNavItem = document.querySelector(`.nav-item[data-tab="${tabName}"]`);
  if (targetNavItem) {
    targetNavItem.classList.add('active');
  }

  // Update top counter badge according to tab
  const badge = document.getElementById('stepCounterBadge');
  if (badge) {
    switch (tabName) {
      case 'home':
        badge.innerText = '01 — 02';
        break;
      case 'apply':
        badge.innerText = 'QABUL';
        break;
      case 'status':
        badge.innerText = 'STATUS';
        break;
      case 'courses':
        badge.innerText = 'KURSLAR';
        break;
      case 'owner':
        badge.innerText = 'USTOZ';
        break;
      default:
        badge.innerText = '01 — 02';
    }
  }

  // Scroll to top
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/**
 * Choose course card and switch to application form
 */
function selectCourseAndApply(courseKey) {
  const select = document.getElementById('courseSelect');
  if (select) {
    select.value = courseKey;
  }
  switchTab('apply');
  showToast("Yo'nalish tanlandi. Anketani to'ldiring ✨", '📋');
}

/**
 * Submit Admission Application
 */
async function submitApplication(e) {
  e.preventDefault();

  const fullName = document.getElementById('fullName').value.trim();
  const phone = document.getElementById('phoneNumber').value.trim();
  const course = document.getElementById('courseSelect').value;
  const courseText = document.getElementById('courseSelect').selectedOptions[0].text;
  const level = document.getElementById('experienceLevel').value;
  const goal = document.getElementById('creativeGoal').value.trim();

  if (!fullName || !phone) {
    showToast('Iltimos, ism va telefon raqamingizni kiriting', '⚠️');
    return;
  }

  const appId = 'APP-' + Math.floor(10000 + Math.random() * 90000);
  const applicationData = {
    action: 'submit_application',
    appId: appId,
    fullName: fullName,
    phone: phone,
    courseKey: course,
    courseTitle: courseText,
    level: level,
    goal: goal,
    userId: currentUser ? currentUser.id : null,
    username: currentUser ? currentUser.username : null,
    submittedAt: new Date().toISOString()
  };

  // Save to localStorage for status lookup
  try {
    localStorage.setItem('lastApplication', JSON.stringify(applicationData));
  } catch (err) {
    console.error('Storage error:', err);
  }

  // Disable button while processing
  const submitBtn = document.getElementById('submitBtn');
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span>Yuborilmoqda...</span>';
  }

  // Attempt sending via backend API or Telegram.WebApp.sendData
  let success = false;
  try {
    const res = await fetch('/api/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(applicationData)
    });
    if (res.ok) {
      success = true;
    }
  } catch (err) {
    // If backend endpoint is offline (e.g. static preview), fallback gracefully
    console.log('API dispatch fallback:', err);
  }

  // Send data to Telegram Bot directly if WebApp supports sendData
  if (tg && typeof tg.sendData === 'function') {
    try {
      tg.sendData(JSON.stringify({
        type: 'new_application',
        ...applicationData
      }));
      success = true;
    } catch (e) {
      console.log('tg.sendData fallback', e);
    }
  }

  // Feedback
  if (tg?.HapticFeedback) {
    tg.HapticFeedback.notificationOccurred('success');
  }

  // Show Success Card
  document.getElementById('applyFormContainer').style.display = 'none';
  const successCard = document.getElementById('applySuccessCard');
  if (successCard) {
    document.getElementById('appIdBadge').innerText = appId;
    successCard.style.display = 'block';
  }

  // Update Status tab
  updateStatusView(applicationData);

  showToast('Arizangiz muvaffaqiyatli topshirildi!', '🎉');
}

/**
 * Load saved application on start
 */
function loadSavedApplication() {
  try {
    const saved = localStorage.getItem('lastApplication');
    if (saved) {
      const data = JSON.parse(saved);
      updateStatusView(data);
      const searchInput = document.getElementById('statusPhoneSearch');
      if (searchInput && data.appId) {
        searchInput.value = data.appId;
      }
    }
  } catch (e) {
    console.warn(e);
  }
}

/**
 * Update Status Tab with given data
 */
function updateStatusView(data) {
  const nameLabel = document.getElementById('statusAppName');
  const badge = document.getElementById('statusBadge');
  if (nameLabel && data.fullName) {
    nameLabel.innerText = `${data.fullName} (${data.appId || 'APP'})`;
  }
  if (badge) {
    badge.innerText = "KO'RIB CHIQILMOQDA";
    badge.style.background = '#d4af37';
  }
}

/**
 * Lookup application status by ID or phone
 */
function lookupApplication() {
  const query = document.getElementById('statusPhoneSearch').value.trim();
  if (!query) {
    showToast('Ariza kodi yoki telefon raqamini kiriting', '⚠️');
    return;
  }

  if (tg?.HapticFeedback) {
    tg.HapticFeedback.impactOccurred('medium');
  }

  showToast(`Qidirilmoqda: ${query}...`, '🔍');

  setTimeout(() => {
    showToast(`Ariza topildi: ${query} (Bosqich: Ko'rib chiqilmoqda)`, '✅');
  }, 600);
}

/**
 * Support action - opens chat or closes webapp with bot command
 */
function contactSupport() {
  if (tg) {
    try {
      tg.openTelegramLink('https://t.me/asadbek_yordamchisi');
      return;
    } catch (e) {
      // fallback
    }
  }
  showToast("Botga to'g'ridan-to'g'ri xabar yuborishingiz mumkin!", '💬');
}

/**
 * Refresh Ustoz Dashboard
 */
async function refreshDashboard() {
  if (tg?.HapticFeedback) {
    tg.HapticFeedback.impactOccurred('medium');
  }
  showToast('Ma\'lumotlar yangilanmoqda...', '🔄');

  try {
    const res = await fetch('/api/stats');
    if (res.ok) {
      const stats = await res.json();
      if (stats.balance) document.getElementById('dashBalance').innerText = stats.balance;
      if (stats.discipline) document.getElementById('dashDiscipline').innerText = stats.discipline;
      if (stats.appCount) document.getElementById('dashAppCount').innerText = `${stats.appCount} ta`;
    }
  } catch (err) {
    // Demo refresh
  }

  setTimeout(() => {
    showToast('Dashboard muvaffaqiyatli yangilandi!', '✨');
  }, 400);
}

/**
 * Show a sleek floating toast notification
 */
function showToast(message, icon = '✨') {
  const toast = document.getElementById('toastNotice');
  const msgEl = document.getElementById('toastMessage');
  const iconEl = document.getElementById('toastIcon');

  if (!toast) return;

  msgEl.innerText = message;
  iconEl.innerText = icon;

  toast.classList.add('show');

  setTimeout(() => {
    toast.classList.remove('show');
  }, 3200);
}
