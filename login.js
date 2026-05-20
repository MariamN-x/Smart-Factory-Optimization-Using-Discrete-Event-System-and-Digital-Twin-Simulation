// static/login.js
function getCsrf() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

function show2FA(show) {
  const g = document.getElementById('totpGroup');
  if (!g) return;
  g.style.display = show ? 'block' : 'none';
  if (show) document.getElementById('totp').focus();
}

document.getElementById('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();

  const email = document.getElementById('email').value.trim();
  const password = document.getElementById('password').value;
  const totp = document.getElementById('totp').value.trim();

  const payload = { email, password };
  if (totp) payload.totp = totp;

  try {
    const res = await fetch('/auth/login', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrf(),
      },
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));

    if (data && data.error === '2fa_required') {
      show2FA(true);
      alert('2FA is enabled. enter your 6-digit code.');
      return;
    }

    if (!res.ok || !data.ok) {
      const msg =
        data.error === 'too_many_attempts' ? 'too many attempts. wait a bit.' :
        data.error === 'invalid_2fa' ? 'wrong 2FA code.' :
        'invalid email or access code.';
      alert(msg);
      return;
    }

    window.location.href = '/';
  } catch (err) {
    alert('server not reachable. is app.py running on port 8055?');
  }
});

// keep your robot hover effect if you want (safe)
document.querySelector('.factory-section').addEventListener('mousemove', (e) => {
  const robot = document.querySelector('.robot-inspector');
  const rect = robot.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  const rotateX = (y - rect.height / 2) / 30;
  const rotateY = (rect.width / 2 - x) / 30;
  robot.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) translateX(0)`;
});

document.querySelector('.factory-section').addEventListener('mouseleave', () => {
  document.querySelector('.robot-inspector').style.transform = 'perspective(1000px) rotateX(0) rotateY(0)';
});
