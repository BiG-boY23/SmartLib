import logging
from unittest.mock import Mock
import httpx
import pytest
import email_service as mail


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    for key in ['SMTP_HOST','SMTP_FROM','SMTP_USERNAME','SMTP_PASSWORD','BREVO_API_KEY','EMAIL_PROVIDER']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('SMTP_FROM','Library <library@example.test>')


def test_brevo_sends_over_https_without_smtp(monkeypatch):
    monkeypatch.setenv('EMAIL_PROVIDER','brevo')
    monkeypatch.setenv('BREVO_API_KEY','private-test-key')
    post=Mock(return_value=httpx.Response(201,json={'messageId':'accepted'}))
    monkeypatch.setattr(mail.httpx,'post',post)
    smtp=Mock(side_effect=AssertionError('Must not connect to SMTP'))
    monkeypatch.setattr(mail.smtplib,'SMTP',smtp)
    mail.send_email('reader@example.test','Verify','Code: 123456')
    args,kw=post.call_args
    assert args[0]=='https://api.brevo.com/v3/smtp/email'
    assert kw['headers']['api-key']=='private-test-key'
    assert kw['json']['sender']=={'email':'library@example.test','name':'Library'}
    assert kw['json']['textContent']=='Code: 123456'
    assert kw['json']['to']==[{'email':'reader@example.test'}]
    assert kw['timeout']==15 and not kw['follow_redirects']


@pytest.mark.parametrize('status',[400,401,403,429,500,302])
def test_brevo_failure_does_not_leak_secrets(monkeypatch,caplog,status):
    monkeypatch.setenv('EMAIL_PROVIDER','brevo');monkeypatch.setenv('BREVO_API_KEY','private-test-key')
    monkeypatch.setattr(mail.httpx,'post',Mock(return_value=httpx.Response(status,text='private-test-key OTP 123456 reader@example.test')))
    with caplog.at_level(logging.WARNING), pytest.raises(mail.EmailDeliveryError):
        mail.send_email('reader@example.test','Verify','123456')
    assert str(status) in caplog.text
    assert all(secret not in caplog.text for secret in ['private-test-key','123456','reader@example.test'])


def test_brevo_timeout_and_missing_credentials(monkeypatch):
    monkeypatch.setenv('EMAIL_PROVIDER','brevo')
    with pytest.raises(mail.EmailConfigurationError):mail.send_email('a@example.test','s','b')
    monkeypatch.setenv('BREVO_API_KEY','key')
    monkeypatch.setattr(mail.httpx,'post',Mock(side_effect=httpx.ConnectTimeout('private error')))
    with pytest.raises(mail.EmailDeliveryError):mail.send_email('a@example.test','s','b')


def test_unknown_provider_fails_closed(monkeypatch):
    monkeypatch.setenv('EMAIL_PROVIDER','typo')
    with pytest.raises(mail.EmailConfigurationError):mail.ensure_smtp_configured()


def test_smtp_remains_default(monkeypatch):
    monkeypatch.setenv('SMTP_HOST','smtp.example.test')
    smtp=Mock();server=smtp.return_value.__enter__=Mock(return_value=Mock())
    smtp.return_value.__exit__=Mock(return_value=False)
    monkeypatch.setattr(mail.smtplib,'SMTP',smtp)
    mail.send_email('reader@example.test','Verify','body')
    smtp.assert_called_once_with('smtp.example.test',587,timeout=15)
    server.return_value.send_message.assert_called_once()
