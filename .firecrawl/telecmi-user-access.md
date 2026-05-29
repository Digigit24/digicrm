[Skip to main content](https://doc.telecmi.com/chub/docs/user-access/#__docusaurus_skipToContent_fallback)

[![TeleCMI Doc](https://doc.telecmi.com/chub/img/logo.svg)](https://doc.telecmi.com/chub/)[Home](https://doc.telecmi.com/) [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview) [Click to call](https://doc.telecmi.com/chub/docs/click-to-call-admin) [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms)

[Examples](https://doc.telecmi.com/chub/docs/webhooks-node) [GitHub](https://github.com/telecmi/)

- [Introduction](https://doc.telecmi.com/chub/docs/user-access/#)

- [Webhooks](https://doc.telecmi.com/chub/docs/user-access/#)

- [API's](https://doc.telecmi.com/chub/docs/user-access/#)

- [Admin Call API](https://doc.telecmi.com/chub/docs/user-access/#)

- [Admin Call Flow API](https://doc.telecmi.com/chub/docs/user-access/#)

- [User Operations](https://doc.telecmi.com/chub/docs/user-access/#)

- [User API](https://doc.telecmi.com/chub/docs/user-access/#)

  - [Login Token](https://doc.telecmi.com/chub/docs/login-token)
  - [User Access API](https://doc.telecmi.com/chub/docs/user-access)
  - [Click-To-Call](https://doc.telecmi.com/chub/docs/click-to-call)
  - [Click-To-Call India](https://doc.telecmi.com/chub/docs/click-to-call-ind)
  - [Click-To-Call Hangup](https://doc.telecmi.com/chub/docs/click-to-call-hangup)
  - [User Hangup](https://doc.telecmi.com/chub/docs/user-hangup)
  - [User Logout](https://doc.telecmi.com/chub/docs/user-logout)
  - [User Incoming Calls](https://doc.telecmi.com/chub/docs/agent-incoming)
  - [User Outgoing Calls](https://doc.telecmi.com/chub/docs/agent-outgoing)
  - [Incoming Missed](https://doc.telecmi.com/chub/docs/agent-incoming-missed)
  - [Incoming Answered](https://doc.telecmi.com/chub/docs/agent-incoming-answered)
  - [Outgoing Missed](https://doc.telecmi.com/chub/docs/agent-outgoing-missed)
  - [Outgoing Answered](https://doc.telecmi.com/chub/docs/agent-outgoing-answered)
  - [Callback](https://doc.telecmi.com/chub/docs/agent-callback)
  - [Callback Action](https://doc.telecmi.com/chub/docs/agent-callback-action)
  - [Get Contact](https://doc.telecmi.com/chub/docs/agent-get-contact)
  - [Update Contact](https://doc.telecmi.com/chub/docs/agent-update-contact)
  - [Delete Contact](https://doc.telecmi.com/chub/docs/agent-delete-contact)
  - [Add Notes](https://doc.telecmi.com/chub/docs/agent-add-notes)
  - [Get Notes](https://doc.telecmi.com/chub/docs/agent-get-notes)
  - [Get Tags](https://doc.telecmi.com/chub/docs/agent-get-tags)
  - [Get Break](https://doc.telecmi.com/chub/docs/agent-get-break)
  - [User CallerID List](https://doc.telecmi.com/chub/docs/agent-callerid-list)
  - [User CallerID Update](https://doc.telecmi.com/chub/docs/agent-callerid-update)
  - [Add Supervisor](https://doc.telecmi.com/chub/docs/add-supervisor)
- [SMS API](https://doc.telecmi.com/chub/docs/user-access/#)

- [App Settings API](https://doc.telecmi.com/chub/docs/user-access/#)

- [SDK](https://doc.telecmi.com/chub/docs/user-access/#)

- [Examples](https://doc.telecmi.com/chub/docs/user-access/#)

- [Tools](https://doc.telecmi.com/chub/docs/user-access/#)


- User API
- User Access API

On this page

# User Access API

Each API request in TeleCMI platform includes [App id](https://doc.telecmi.com/chub/docs/app-auth) and [secret](https://doc.telecmi.com/chub/docs/app-auth). Get your [App id](https://doc.telecmi.com/chub/docs/app-auth) and [secret](https://doc.telecmi.com/chub/docs/app-auth) in [CHUB dashboard](https://connle.telecmi.com/login). After getting the app id and secret, make a **POST** request to the below base URL to generate the unique admin secret. Using unique admin secret, get the live call activities of the users and barge to the live calls.

## Base URL [​](https://doc.telecmi.com/chub/docs/user-access/\#base-url "Direct link to Base URL")

Send your **POST** method request with valid parameters, to the following base URL.

```http
https://rest.telecmi.com/v2/token
```

## Required Parameters [​](https://doc.telecmi.com/chub/docs/user-access/\#required-parameters "Direct link to Required Parameters")

These are the required **POST** method parameters with description

| Parameter Name | Type | Description |
| --- | --- | --- |
| appid | number | Your app ID |
| secret | string | Your app secret |

##### Note

All the above parameters are mandatory.

## Sample JSON Request [​](https://doc.telecmi.com/chub/docs/user-access/\#sample-json-request "Direct link to Sample JSON Request")

Below is the following sample JSON **POST** method request

```javascript
{
  "appid": 1111112,
  "secret": "xxxx-xxxx-xxxx-xxxx"
}
```

## Sample Response [​](https://doc.telecmi.com/chub/docs/user-access/\#sample-response "Direct link to Sample Response")

If the provided information is valid, your web server will get a sample response from TeleCMI Platform as given below

```javascript
{
    "secret": "xxxx-xxxx-xxxx-xxxx",
    "expiresIn": "30d",
    "code": 200
}
```

## Properties [​](https://doc.telecmi.com/chub/docs/user-access/\#properties "Direct link to Properties")

These are the list of properties and its description

| Property | Type | Description |
| --- | --- | --- |
| secret | string | The unique admin secret. |
| expiresIn | string | The unique admin secret expiry details. By default 30 Days. |

## HTTP status codes [​](https://doc.telecmi.com/chub/docs/user-access/\#http-status-codes "Direct link to HTTP status codes")

TeleCMI API platform represents the following status code to identity the errors.

| Status code | Status type | Description |
| --- | --- | --- |
| 200 | OK | We received the request |
| 404 | Error | Invalid app id or secret, authentication failed |
| 400 | Error | Parameter missing |

[Previous\\
\\
Login Token](https://doc.telecmi.com/chub/docs/login-token) [Next\\
\\
Click-To-Call](https://doc.telecmi.com/chub/docs/click-to-call)

- [Base URL](https://doc.telecmi.com/chub/docs/user-access/#base-url)
- [Required Parameters](https://doc.telecmi.com/chub/docs/user-access/#required-parameters)
- [Sample JSON Request](https://doc.telecmi.com/chub/docs/user-access/#sample-json-request)
- [Sample Response](https://doc.telecmi.com/chub/docs/user-access/#sample-response)
- [Properties](https://doc.telecmi.com/chub/docs/user-access/#properties)
- [HTTP status codes](https://doc.telecmi.com/chub/docs/user-access/#http-status-codes)