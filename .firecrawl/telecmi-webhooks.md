[Skip to main content](https://doc.telecmi.com/chub/docs/webhooks-overview/#__docusaurus_skipToContent_fallback)

[![TeleCMI Doc](https://doc.telecmi.com/chub/img/logo.svg)](https://doc.telecmi.com/chub/)[Home](https://doc.telecmi.com/) [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview) [Click to call](https://doc.telecmi.com/chub/docs/click-to-call-admin) [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms)

[Examples](https://doc.telecmi.com/chub/docs/webhooks-node) [GitHub](https://github.com/telecmi/)

- [Introduction](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

- [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

  - [Overview](https://doc.telecmi.com/chub/docs/webhooks-overview)
  - [Incoming CDR](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

  - [Outgoing CDR](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

  - [Incoming Live Events](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

  - [Outgoing Live Events](https://doc.telecmi.com/chub/docs/webhooks-overview/#)
- [API's](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

- [Admin Call API](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

- [Admin Call Flow API](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

- [User Operations](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

- [User API](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

- [SMS API](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

- [App Settings API](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

- [SDK](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

- [Examples](https://doc.telecmi.com/chub/docs/webhooks-overview/#)

- [Tools](https://doc.telecmi.com/chub/docs/webhooks-overview/#)


- Webhooks
- Overview

On this page

# Webhooks Overview

### What is TeleCMI webhooks ? [​](https://doc.telecmi.com/chub/docs/webhooks-overview/\#what-is-telecmi-webhooks- "Direct link to What is TeleCMI webhooks ?")

Webhooks are user-defined HTTP callbacks, TeleCMI usually sends POST or GET requests to your webserver URL when an incoming or outgoing call happens. TeleCMI Webhooks magical data is sent as a JSON, to your configured web server POST or GET method URL. Using this JSON data you can build an analysis dashboard, monitor user's calls and customize your report.

Our TeleCMI platform workflow,

![Webhooks](https://doc.telecmi.com/chub/assets/images/webhook-flow-bb7565e5d30767aca88014959a3b41bc.png)

### How webhooks works in the telecmi platform ? [​](https://doc.telecmi.com/chub/docs/webhooks-overview/\#how-webhooks-works-in-the-telecmi-platform- "Direct link to How webhooks works in the telecmi platform ?")

Configure your web server POST or GET method URL in the [TeleCMI dashboard](https://connle.telecmi.com/login). Once your configuration is completed, our TeleCMI platform will notify the [call detail record(CDR)](https://doc.telecmi.com/chub/docs/webhooks-overview#what-is-cdr-) and [live events](https://doc.telecmi.com/chub/docs/webhooks-overview#what-is-live-events-).

### What is CDR ? [​](https://doc.telecmi.com/chub/docs/webhooks-overview/\#what-is-cdr- "Direct link to What is CDR ?")

The CDR is also known as call detail record. We usually notify your webserver POST or GET method URL when an incoming or outgoing call is completed.

### What is Live Events ? [​](https://doc.telecmi.com/chub/docs/webhooks-overview/\#what-is-live-events- "Direct link to What is Live Events ?")

The live events are the details of live ongoing calls. We usually notify your webserver POST or GET method URL for the ongoing incoming or outgoing call.

### How to setup webhooks ? [​](https://doc.telecmi.com/chub/docs/webhooks-overview/\#how-to-setup-webhooks- "Direct link to How to setup webhooks ?")

To set up a webhooks follow the below steps.

1. Login into the [TeleCMI dashboard](https://connle.telecmi.com/login).

2. Your business number will be displayed in the panel, click on the business number.

3. Go to SETTINGS --> WEBHOOKS

4. Click the add button, select the type as call report or notify and select the method as POST or GET.

5. Now configure your webserver URL and save it.


![Webhooks](https://doc.telecmi.com/chub/assets/images/webhook-27df4ad77aa4f18e2c3510f164cc895a.png)

note

- Your web server URL is the POST or GET method.
- Make sure your web server URL is accessible from the internet(public).

If you need to test locally use our simple web servers ( [Node.JS](https://doc.telecmi.com/chub/docs/webhooks-node), [Python](https://doc.telecmi.com/chub/docs/webhooks-python), [Java](https://doc.telecmi.com/chub/docs/webhooks-java), [PHP](https://doc.telecmi.com/chub/docs/webhooks-php) ) and [Ngrok](https://doc.telecmi.com/chub/docs/ngrok) for development purpose.

Now your webhook setup is ready to receive call detail record(CDR) from the TeleCMI platform.

[Previous\\
\\
App Authentication](https://doc.telecmi.com/chub/docs/app-auth) [Next\\
\\
Missed](https://doc.telecmi.com/chub/docs/incoming-missed)

- [What is TeleCMI webhooks ?](https://doc.telecmi.com/chub/docs/webhooks-overview/#what-is-telecmi-webhooks-)
- [How webhooks works in the telecmi platform ?](https://doc.telecmi.com/chub/docs/webhooks-overview/#how-webhooks-works-in-the-telecmi-platform-)
- [What is CDR ?](https://doc.telecmi.com/chub/docs/webhooks-overview/#what-is-cdr-)
- [What is Live Events ?](https://doc.telecmi.com/chub/docs/webhooks-overview/#what-is-live-events-)
- [How to setup webhooks ?](https://doc.telecmi.com/chub/docs/webhooks-overview/#how-to-setup-webhooks-)