[Skip to main content](https://doc.telecmi.com/chub/docs/agent-callback/#__docusaurus_skipToContent_fallback)

[![TeleCMI Doc](https://doc.telecmi.com/chub/img/logo.svg)](https://doc.telecmi.com/chub/)[Home](https://doc.telecmi.com/) [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview) [Click to call](https://doc.telecmi.com/chub/docs/click-to-call-admin) [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms)

[Examples](https://doc.telecmi.com/chub/docs/webhooks-node) [GitHub](https://github.com/telecmi/)

- [Introduction](https://doc.telecmi.com/chub/docs/agent-callback/#)

- [Webhooks](https://doc.telecmi.com/chub/docs/agent-callback/#)

- [API's](https://doc.telecmi.com/chub/docs/agent-callback/#)

- [Admin Call API](https://doc.telecmi.com/chub/docs/agent-callback/#)

- [Admin Call Flow API](https://doc.telecmi.com/chub/docs/agent-callback/#)

- [User Operations](https://doc.telecmi.com/chub/docs/agent-callback/#)

- [User API](https://doc.telecmi.com/chub/docs/agent-callback/#)

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
- [SMS API](https://doc.telecmi.com/chub/docs/agent-callback/#)

- [App Settings API](https://doc.telecmi.com/chub/docs/agent-callback/#)

- [SDK](https://doc.telecmi.com/chub/docs/agent-callback/#)

- [Examples](https://doc.telecmi.com/chub/docs/agent-callback/#)

- [Tools](https://doc.telecmi.com/chub/docs/agent-callback/#)


- User API
- Callback

On this page

# Callback

Each user API request in TeleCMI platform includes [user login token](https://doc.telecmi.com/chub/docs/login-token). After getting the [user login token](https://doc.telecmi.com/chub/docs/login-token), make a **POST** request to the below base URL to retrieve the callback list.

## Base URL [​](https://doc.telecmi.com/chub/docs/agent-callback/\#base-url "Direct link to Base URL")

Send your **POST** method request with valid parameters, to the following base URL.

```text
https://rest.telecmi.com/v2/callback
```

## Required Parameters [​](https://doc.telecmi.com/chub/docs/agent-callback/\#required-parameters "Direct link to Required Parameters")

These are the required **POST** method parameters with description

| Parameter Name | Type | Description |
| --- | --- | --- |
| \*token | string | Your [user token](https://doc.telecmi.com/chub/docs/login-token) |
| \*from | number | The timestamp of from date and time in UTC timezone. |
| \*to | number | The timestamp of to date and time in UTC timezone. |
| page | number | The Number of page per 10 record. By default the page is 1. |
| limit | number | The Number of record for request, By default the limit is 10. Maximum limit is 10. |

##### Note

The \* marked parameter is mandatory.

## Sample JSON Request [​](https://doc.telecmi.com/chub/docs/agent-callback/\#sample-json-request "Direct link to Sample JSON Request")

Below is the following sample JSON **POST** method request

```javascript
{
  "token": "xxxx-xxxx-xxxx-xxxx",
  "from": 1636615799000,
  "to": 1636702199000,
  "page": 1,
  "limit": 5
}
```

## Sample Response [​](https://doc.telecmi.com/chub/docs/agent-callback/\#sample-response "Direct link to Sample Response")

If the provided information is valid, your web server will get a sample response from TeleCMI Platform as given below

```javascript
 {
    "code": 200,
    "count": 1,
    "cdr": [\
        {\
            "_id": "618e17aab5abcd0d0f4b0746",\
            "from": 9200000000,\
            "time": 1636702119000\
        }\
    ]
}
```

## Properties [​](https://doc.telecmi.com/chub/docs/agent-callback/\#properties "Direct link to Properties")

These are the list of properties and its description

| Property | Type | Description |
| --- | --- | --- |
| count | number | The total count of call detail record(cdr) available |
| cdr | JSON array | The list of total cdr in detail |
| \_id | string | A unique identifier of this call |
| from | number | The number the call came from |
| time | number | Timestamp of the call in UTC timezone. |

## HTTP status codes [​](https://doc.telecmi.com/chub/docs/agent-callback/\#http-status-codes "Direct link to HTTP status codes")

TeleCMI API platform represents the following status code to identity the errors.

| Status code | Status type | Description |
| --- | --- | --- |
| 200 | OK | We received the request |
| 404 | Error | Invalid user token, Failed to authenticate token |
| 400 | Error | Parameter missing, Parameter limit lesser then equals 10 |

[Previous\\
\\
Outgoing Answered](https://doc.telecmi.com/chub/docs/agent-outgoing-answered) [Next\\
\\
Callback Action](https://doc.telecmi.com/chub/docs/agent-callback-action)

- [Base URL](https://doc.telecmi.com/chub/docs/agent-callback/#base-url)
- [Required Parameters](https://doc.telecmi.com/chub/docs/agent-callback/#required-parameters)
- [Sample JSON Request](https://doc.telecmi.com/chub/docs/agent-callback/#sample-json-request)
- [Sample Response](https://doc.telecmi.com/chub/docs/agent-callback/#sample-response)
- [Properties](https://doc.telecmi.com/chub/docs/agent-callback/#properties)
- [HTTP status codes](https://doc.telecmi.com/chub/docs/agent-callback/#http-status-codes)