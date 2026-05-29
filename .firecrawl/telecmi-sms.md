[Skip to main content](https://doc.telecmi.com/chub/docs/agent-send-sms/#__docusaurus_skipToContent_fallback)

[![TeleCMI Doc](https://doc.telecmi.com/chub/img/logo.svg)](https://doc.telecmi.com/chub/)[Home](https://doc.telecmi.com/) [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview) [Click to call](https://doc.telecmi.com/chub/docs/click-to-call-admin) [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms)

[Examples](https://doc.telecmi.com/chub/docs/webhooks-node) [GitHub](https://github.com/telecmi/)

- [Introduction](https://doc.telecmi.com/chub/docs/agent-send-sms/#)

- [Webhooks](https://doc.telecmi.com/chub/docs/agent-send-sms/#)

- [API's](https://doc.telecmi.com/chub/docs/agent-send-sms/#)

- [Admin Call API](https://doc.telecmi.com/chub/docs/agent-send-sms/#)

- [Admin Call Flow API](https://doc.telecmi.com/chub/docs/agent-send-sms/#)

- [User Operations](https://doc.telecmi.com/chub/docs/agent-send-sms/#)

- [User API](https://doc.telecmi.com/chub/docs/agent-send-sms/#)

- [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms/#)

  - [User Send SMS](https://doc.telecmi.com/chub/docs/agent-send-sms)
  - [User Inbox](https://doc.telecmi.com/chub/docs/agent-inbox)
  - [User Unread SMS](https://doc.telecmi.com/chub/docs/agent-unread-sms)
  - [User Update SMS Status](https://doc.telecmi.com/chub/docs/agent-update-sms)
- [App Settings API](https://doc.telecmi.com/chub/docs/agent-send-sms/#)

- [SDK](https://doc.telecmi.com/chub/docs/agent-send-sms/#)

- [Examples](https://doc.telecmi.com/chub/docs/agent-send-sms/#)

- [Tools](https://doc.telecmi.com/chub/docs/agent-send-sms/#)


- SMS API
- User Send SMS

On this page

# User Send SMS

Each user API request in TeleCMI platform includes [user login token](https://doc.telecmi.com/chub/docs/login-token). After getting the [user login token](https://doc.telecmi.com/chub/docs/login-token), make a **POST** request to the below base URL to send SMS.

## Base URL [​](https://doc.telecmi.com/chub/docs/agent-send-sms/\#base-url "Direct link to Base URL")

Send your **POST** method request with valid parameters, to the following base URL.

```text
https://rest.telecmi.com/v2/messages
```

## Required Parameters [​](https://doc.telecmi.com/chub/docs/agent-send-sms/\#required-parameters "Direct link to Required Parameters")

These are the required **POST** method parameters with description

| Parameter Name | Type | Description |
| --- | --- | --- |
| token | string | Your [user token](https://doc.telecmi.com/chub/docs/login-token) |
| text | string | SMS text message content |
| to | string | The Phone number to receive message |

##### Note

All the above parameters are mandatory.

## Sample JSON Request [​](https://doc.telecmi.com/chub/docs/agent-send-sms/\#sample-json-request "Direct link to Sample JSON Request")

Below is the following sample JSON **POST** method request

```javascript
{
"token": "xxxx-xxxx-xxxx-xxxx",
"text": "Your appointment is scheduled at 10 AM",
"to": 19170000000
}
```

## Sample Response [​](https://doc.telecmi.com/chub/docs/agent-send-sms/\#sample-response "Direct link to Sample Response")

If the provided information is valid, your web server will get a sample response from TeleCMI Platform as given below

```javascript
{
    "code": 200,
    "msg": "message sent"
}
```

## Properties [​](https://doc.telecmi.com/chub/docs/agent-send-sms/\#properties "Direct link to Properties")

These are the list of properties and its description

| Property | Type | Description |
| --- | --- | --- |
| msg | string | The message has been sent |

## HTTP status codes [​](https://doc.telecmi.com/chub/docs/agent-send-sms/\#http-status-codes "Direct link to HTTP status codes")

TeleCMI API platform represents the following status code to identity the errors.

| Status code | Status Type | Description |
| --- | --- | --- |
| 200 | Success | We received the request |
| 404 | Error | Invalid user token, Failed to authenticate token |
| 400 | Error | Parameter missing |
| 401 | Error | Not allowed to send US SMS |
| 407 | Error | Invalid sender ID |

[Previous\\
\\
Add Supervisor](https://doc.telecmi.com/chub/docs/add-supervisor) [Next\\
\\
User Inbox](https://doc.telecmi.com/chub/docs/agent-inbox)

- [Base URL](https://doc.telecmi.com/chub/docs/agent-send-sms/#base-url)
- [Required Parameters](https://doc.telecmi.com/chub/docs/agent-send-sms/#required-parameters)
- [Sample JSON Request](https://doc.telecmi.com/chub/docs/agent-send-sms/#sample-json-request)
- [Sample Response](https://doc.telecmi.com/chub/docs/agent-send-sms/#sample-response)
- [Properties](https://doc.telecmi.com/chub/docs/agent-send-sms/#properties)
- [HTTP status codes](https://doc.telecmi.com/chub/docs/agent-send-sms/#http-status-codes)