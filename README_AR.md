# Global Missile & Drone OSINT Alerts — GitHub V1

نسخة تجريبية تعمل على GitHub Actions وترسل تنبيهات الصواريخ والمسيّرات إلى قناة Telegram.

## الإعداد السريع

1. أنشئ Repository جديد في GitHub وارفع محتويات هذا المجلد كما هي.
2. أنشئ Telegram Bot من BotFather.
3. أضف البوت إلى القناة كـ Administrator واسمح له بالنشر.
4. في GitHub اذهب إلى:
   Settings → Secrets and variables → Actions → New repository secret
5. أضف سرين:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID` مثل `@YourChannelName`
6. اذهب إلى Actions → Global OSINT Missile & Drone Alerts → Run workflow.
7. بعد نجاح الاختبار، الـ workflow يحاول التشغيل كل 5 دقائق.

## مستويات الثقة

- ✅ Confirmed / مؤكد
- 🟡 Likely / مرجح
- ⚪ Unverified / غير مؤكد

النظام يجمع البلاغات المتشابهة، يرفع الثقة عند وجود مصادر مستقلة، ويمنع تكرار الحدث. إذا ارتفعت حالته لاحقًا، يرسل Update.

## مهم

هذه OSINT وليست رادارًا عسكريًا، وGitHub Actions قد يتأخر أحيانًا؛ لذلك النسخة مناسبة لاختبار الفكرة وليست بديلًا عن الإنذارات الرسمية.

## إذا فشل تحديث state/seen.json

اذهب إلى:
Settings → Actions → General → Workflow permissions
وفعّل Read and write permissions.

## التحكم في الضوضاء

داخل `.github/workflows/osint-alerts.yml`:

- `MIN_CONFIDENCE: "Unverified"` يرسل كل المستويات.
- غيّرها إلى `Likely` لإرسال المرجح والمؤكد فقط.
- غيّرها إلى `Confirmed` للمؤكد فقط.
- `MAX_ALERTS: "10"` يمنع إرسال أكثر من 10 تنبيهات في التشغيل الواحد.
