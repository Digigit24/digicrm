[Skip to main content](https://doc.telecmi.com/chub/docs/click-to-call/#__docusaurus_skipToContent_fallback)

[![TeleCMI Doc](https://doc.telecmi.com/chub/img/logo.svg)](https://doc.telecmi.com/chub/)[Home](https://doc.telecmi.com/) [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview) [Click to call](https://doc.telecmi.com/chub/docs/click-to-call-admin) [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms)

[Examples](https://doc.telecmi.com/chub/docs/webhooks-node) [GitHub](https://github.com/telecmi/)

- [Introduction](https://doc.telecmi.com/chub/docs/click-to-call/#)

- [Webhooks](https://doc.telecmi.com/chub/docs/click-to-call/#)

- [API's](https://doc.telecmi.com/chub/docs/click-to-call/#)

- [Admin Call API](https://doc.telecmi.com/chub/docs/click-to-call/#)

- [Admin Call Flow API](https://doc.telecmi.com/chub/docs/click-to-call/#)

- [User Operations](https://doc.telecmi.com/chub/docs/click-to-call/#)

- [User API](https://doc.telecmi.com/chub/docs/click-to-call/#)

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
- [SMS API](https://doc.telecmi.com/chub/docs/click-to-call/#)

- [App Settings API](https://doc.telecmi.com/chub/docs/click-to-call/#)

- [SDK](https://doc.telecmi.com/chub/docs/click-to-call/#)

- [Examples](https://doc.telecmi.com/chub/docs/click-to-call/#)

- [Tools](https://doc.telecmi.com/chub/docs/click-to-call/#)


- User API
- Click-To-Call

On this page

# Click-To-Call

The Click-To-Call API is used to connect the call, between TeleCMI softphone and phone number. Using [user login token](https://doc.telecmi.com/chub/docs/login-token) you can able to make a Click-To-Call API request. It connects the TeleCMI user softphone first. Once the person at the **From** end picks up the call, then it will connect the **To** number provided by you. After getting the [user login token](https://doc.telecmi.com/chub/docs/login-token), make a **POST** request to the below base URL to initiate the Click-To-Call.

## Base URL [​](https://doc.telecmi.com/chub/docs/click-to-call/\#base-url "Direct link to Base URL")

Send your **POST** method request with valid parameters, to the following base URL.

```text
https://rest.telecmi.com/v2/click2call
```

## Sample JSON Request [​](https://doc.telecmi.com/chub/docs/click-to-call/\#sample-json-request "Direct link to Sample JSON Request")

Below is the following sample JSON **POST** method request

```javascript
{
  "token": "xxxx-xxxx-xxxx-xxxx",
  "to": 19170000000,
  "extra_params": {"crm": "true"},
  "callerid":13090000000
}
```

## Required Parameters [​](https://doc.telecmi.com/chub/docs/click-to-call/\#required-parameters "Direct link to Required Parameters")

These are the required **POST** method parameters with description

| Parameter Name | Type | Description |
| --- | --- | --- |
| \*token | string | Your [user token](https://doc.telecmi.com/chub/docs/login-token) |
| \*to | number | The destination number you need to call |
| extra\_params | JSON | Your custom parameters |
| callerid | number | It defines the caller id of this call. By default, the user selected callerid will be displayed. |

##### Note

The \* marked parameter is mandatory.

## Sample Response [​](https://doc.telecmi.com/chub/docs/click-to-call/\#sample-response "Direct link to Sample Response")

If the provided information is valid, your web server will get a sample response from TeleCMI Platform as given below

```javascript
{
    "code": 200,
    "msg": "Call initiated",
    "request_id": "s96C6XK1BUHX0oVZfOo5NhoJfZZJd0y1nmrNN6dhdkW"
}
```

## Properties [​](https://doc.telecmi.com/chub/docs/click-to-call/\#properties "Direct link to Properties")

These are the list of properties and its description

| Property | Type | Description |
| --- | --- | --- |
| msg | string | The Call initiated |
| request\_id | string | The unique ID for this call |

## HTTP status codes [​](https://doc.telecmi.com/chub/docs/click-to-call/\#http-status-codes "Direct link to HTTP status codes")

TeleCMI API platform represents the following status code to identity the errors.

| Status code | Status Type | Description |
| --- | --- | --- |
| 200 | OK | We received the request |
| 404 | Error | Invalid Number, failed to authenticate token |
| 400 | Error | To parameter missing |

[Previous\\
\\
User Access API](https://doc.telecmi.com/chub/docs/user-access) [Next\\
\\
Click-To-Call India](https://doc.telecmi.com/chub/docs/click-to-call-ind)

- [Base URL](https://doc.telecmi.com/chub/docs/click-to-call/#base-url)
- [Sample JSON Request](https://doc.telecmi.com/chub/docs/click-to-call/#sample-json-request)
- [Required Parameters](https://doc.telecmi.com/chub/docs/click-to-call/#required-parameters)
- [Sample Response](https://doc.telecmi.com/chub/docs/click-to-call/#sample-response)
- [Properties](https://doc.telecmi.com/chub/docs/click-to-call/#properties)
- [HTTP status codes](https://doc.telecmi.com/chub/docs/click-to-call/#http-status-codes)