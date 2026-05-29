[Skip to main content](https://doc.telecmi.com/chub/docs/login-token/#__docusaurus_skipToContent_fallback)

[![TeleCMI Doc](https://doc.telecmi.com/chub/img/logo.svg)](https://doc.telecmi.com/chub/)[Home](https://doc.telecmi.com/) [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview) [Click to call](https://doc.telecmi.com/chub/docs/click-to-call-admin) [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms)

[Examples](https://doc.telecmi.com/chub/docs/webhooks-node) [GitHub](https://github.com/telecmi/)

- [Introduction](https://doc.telecmi.com/chub/docs/login-token/#)

- [Webhooks](https://doc.telecmi.com/chub/docs/login-token/#)

- [API's](https://doc.telecmi.com/chub/docs/login-token/#)

- [Admin Call API](https://doc.telecmi.com/chub/docs/login-token/#)

- [Admin Call Flow API](https://doc.telecmi.com/chub/docs/login-token/#)

- [User Operations](https://doc.telecmi.com/chub/docs/login-token/#)

- [User API](https://doc.telecmi.com/chub/docs/login-token/#)

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
- [SMS API](https://doc.telecmi.com/chub/docs/login-token/#)

- [App Settings API](https://doc.telecmi.com/chub/docs/login-token/#)

- [SDK](https://doc.telecmi.com/chub/docs/login-token/#)

- [Examples](https://doc.telecmi.com/chub/docs/login-token/#)

- [Tools](https://doc.telecmi.com/chub/docs/login-token/#)


- User API
- Login Token

On this page

# User Login API

To make a user API request you need to generate the user token. The user token can generated using user id and password, get the user id and password in the [TeleCMI dashboard](https://connle.telecmi.com/login). After getting the user id and password, make a **POST** request to the below base URL to generate the unique user token.

## Base URL [​](https://doc.telecmi.com/chub/docs/login-token/\#base-url "Direct link to Base URL")

Send your **POST** method request with valid parameters, to the following base URL.

```text
https://rest.telecmi.com/v2/user/login
```

## Required Parameters [​](https://doc.telecmi.com/chub/docs/login-token/\#required-parameters "Direct link to Required Parameters")

These are the required **POST** method parameters with description

| Parameter Name | Type | Description |
| --- | --- | --- |
| id | string | Your user id |
| password | string | Your user password |

##### Note

All the above parameters are mandatory.

## Sample JSON Request [​](https://doc.telecmi.com/chub/docs/login-token/\#sample-json-request "Direct link to Sample JSON Request")

Below is the following sample JSON **POST** method request

```javascript
{
  "id": "103_1111112",
  "password": "abc123"
}
```

## Sample Response [​](https://doc.telecmi.com/chub/docs/login-token/\#sample-response "Direct link to Sample Response")

If the provided information is valid, your web server will get a sample response from TeleCMI Platform as given below

```javascript
{
  "code": 200,
  "msg": "User loged in successfully",
  "token": "xxxx-xxxx-xxxx-xxxx",
  "agent": {
    "category": "inr",
    "id": "103_1111113",
    "inet_no": 1111113,
    "name": "Prasath Sekar"
  }
}
```

## Properties [​](https://doc.telecmi.com/chub/docs/login-token/\#properties "Direct link to Properties")

These are the list of properties and its description

| Property | Type | Description |
| --- | --- | --- |
| msg | string | Your user id and password is authenticated and logged in sucessfully |
| token | string | Your user unique token |
| agent | JSON array | Your user information in detail |
| category | string | The app type. It can be inr or usd |
| id | string | Your user id |
| inet\_no | number | Your app id |
| Name | string | Your user name |

##### Note

Token will automatically expire after 30 days.

## HTTP status codes [​](https://doc.telecmi.com/chub/docs/login-token/\#http-status-codes "Direct link to HTTP status codes")

TeleCMI API platform represents the following status code to identity the errors.

| Status code | Status Type | Description |
| --- | --- | --- |
| 200 | OK | We received the request |
| 404 | Error | Invalid user id or password, authentication failed |

[Previous\\
\\
Remove User](https://doc.telecmi.com/chub/docs/v3-agent-remove-agent) [Next\\
\\
User Access API](https://doc.telecmi.com/chub/docs/user-access)

- [Base URL](https://doc.telecmi.com/chub/docs/login-token/#base-url)
- [Required Parameters](https://doc.telecmi.com/chub/docs/login-token/#required-parameters)
- [Sample JSON Request](https://doc.telecmi.com/chub/docs/login-token/#sample-json-request)
- [Sample Response](https://doc.telecmi.com/chub/docs/login-token/#sample-response)
- [Properties](https://doc.telecmi.com/chub/docs/login-token/#properties)
- [HTTP status codes](https://doc.telecmi.com/chub/docs/login-token/#http-status-codes)