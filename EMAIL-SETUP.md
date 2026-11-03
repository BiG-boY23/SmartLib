# Verification email on Render

The app uses SMTP by default. Render Free blocks outbound SMTP ports 25, 465 and 587. PostgreSQL does not change this restriction. The screenshot's generic delivery error does not distinguish a blocked connection from invalid credentials; server logs now record the transport and a safe error category/status, without keys or message bodies.

## HTTPS delivery with Brevo
1. Create a Brevo account and complete its transactional-email activation requirements.
2. Add and verify an authorized sender. Follow Brevo's domain authentication requirements. If you want an evsu.edu.ph sender, coordinate DNS authentication with the university administrator; do not claim a domain you do not control.
3. Create a Brevo API key (not an SMTP password).
4. Deploy this code and set these variables on the Render WEB SERVICE:

```dotenv
EMAIL_PROVIDER=brevo
BREVO_API_KEY=YOUR_PRIVATE_BREVO_API_KEY
SMTP_FROM=YOUR_VERIFIED_SENDER_EMAIL
PUBLIC_BASE_URL=https://smartlib-5k6s.onrender.com
```

Existing SMTP_HOST/PORT/USERNAME/PASSWORD are ignored when EMAIL_PROVIDER=brevo. Keep keys only in Render; do not put them into Expo variables, the APK, GitHub or screenshots. No APK rebuild is required for this backend transport change.

Save and deploy. Register with a real test email, verify the code, and confirm it cannot be reused. Check the Brevo transactional logs for delivery status (provider acceptance does not guarantee inbox arrival). Also test password reset. Check spam if the provider reports delivery.

Safe Render log examples: provider=brevo HTTP=401 means the API rejected authentication; HTTP=403 may require account/sender authorization; HTTP=429 indicates rate/quota limits. Consult the provider dashboard for details. SMTP timeout errors on a Free Render service are consistent with its blocked ports.

SMTP can still be used with EMAIL_PROVIDER=smtp on hosting that permits your SMTP port and with valid sender credentials. Upgrading just the DATABASE does not upgrade the WEB SERVICE.

No live email has been sent or provider account created by this change. The previously exposed Gmail app password should be revoked; .env.example now contains empty credential placeholders.

References:
https://render.com/docs/free
https://developers.brevo.com/reference/send-transac-email
