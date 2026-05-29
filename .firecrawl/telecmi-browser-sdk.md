[Skip to main content](https://doc.telecmi.com/chub/docs/browser-sdk/#__docusaurus_skipToContent_fallback)

[![TeleCMI Doc](https://doc.telecmi.com/chub/img/logo.svg)](https://doc.telecmi.com/chub/)[Home](https://doc.telecmi.com/) [Webhooks](https://doc.telecmi.com/chub/docs/webhooks-overview) [Click to call](https://doc.telecmi.com/chub/docs/click-to-call-admin) [SMS API](https://doc.telecmi.com/chub/docs/agent-send-sms)

[Examples](https://doc.telecmi.com/chub/docs/webhooks-node) [GitHub](https://github.com/telecmi/)

- [Introduction](https://doc.telecmi.com/chub/docs/browser-sdk/#)

- [Webhooks](https://doc.telecmi.com/chub/docs/browser-sdk/#)

- [API's](https://doc.telecmi.com/chub/docs/browser-sdk/#)

- [Admin Call API](https://doc.telecmi.com/chub/docs/browser-sdk/#)

- [Admin Call Flow API](https://doc.telecmi.com/chub/docs/browser-sdk/#)

- [User Operations](https://doc.telecmi.com/chub/docs/browser-sdk/#)

- [User API](https://doc.telecmi.com/chub/docs/browser-sdk/#)

- [SMS API](https://doc.telecmi.com/chub/docs/browser-sdk/#)

- [App Settings API](https://doc.telecmi.com/chub/docs/browser-sdk/#)

- [SDK](https://doc.telecmi.com/chub/docs/browser-sdk/#)

  - [WebRTC Browser SDK](https://doc.telecmi.com/chub/docs/browser-sdk)
  - [Voice Feed SDK](https://doc.telecmi.com/chub/docs/voice-feed-sdk)
  - [Monipy SDK](https://doc.telecmi.com/chub/docs/monipy-sdk)
- [Examples](https://doc.telecmi.com/chub/docs/browser-sdk/#)

- [Tools](https://doc.telecmi.com/chub/docs/browser-sdk/#)


- SDK
- WebRTC Browser SDK

On this page

# WebRTC SDK

## PIOPIY Client JS SDK for voice [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#piopiy-client-js-sdk-for-voice "Direct link to PIOPIY Client JS SDK for voice")

PIOPIY WebRTC SDK allows you to make and receive voice calls, where making voice calls can be made to a public switched telephone network(PSTN), APP to APP calling and browser to browser calling.

## Package Installation [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#package-installation "Direct link to Package Installation")

### Using NPM [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#using-npm "Direct link to Using NPM")

```javascript
npm install piopiyjs
```

### Using YARN [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#using-yarn "Direct link to Using YARN")

```javascript
yarn add piopiyjs
```

## Monolithic Import [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#monolithic-import "Direct link to Monolithic Import")

### In Browser [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#in-browser "Direct link to In Browser")

#### Clone the repository [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#clone-the-repository "Direct link to Clone the repository")

Use command **git clone** to clone the SDK from our [TeleCMI github repository](https://github.com/telecmi/piopiy_client_js).

```bash
git clone https://github.com/telecmi/piopiy_client_js.git
```

#### Add SDK library to your webpage [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#add-sdk-library-to-your-webpage "Direct link to Add SDK library to your webpage")

```javascript
<script src="dist/piopiy.min.js" type="text/javascript"></script>
```

### In ESM/Typescript [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#in-esmtypescript "Direct link to In ESM/Typescript")

```javascript
import PIOPIY from 'piopiyjs';
```

### In CommonJS [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#in-commonjs "Direct link to In CommonJS")

```javascript
var PIOPIY = require('piopiyjs');
```

## Get Started [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#get-started "Direct link to Get Started")

### Initializing the PIOPIY SDK Object [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#initializing-the-piopiy-sdk-object "Direct link to Initializing the PIOPIY SDK Object")

```javascript
var piopiy = new PIOPIY( {
        name: 'Display Name',
        debug: false,
        autoplay: true,
        ringTime: 60
    } );
```

## Configuration Parameters [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#configuration-parameters "Direct link to Configuration Parameters")

Below is the configuration parameters

| Attribute | Description | Allowed Values | Default Value |
| --- | --- | --- | --- |
| name | Your Display Name in App | string | none |
| debug | Enable debug message in browser console | Boolean | false |
| autoplay | Handle media stream automatically | Boolean | true |
| ringTime | Your incoming call ringing time in seconds | number | 60 |

## PIOPIY Methods [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#piopiy-methods "Direct link to PIOPIY Methods")

### Login [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#login "Direct link to Login")

Using this method user can able to connect with TeleCMI SBC.

```javascript
piopiy.login('user_id','password','SBC_URI');
```

#### Configuration Parameters [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#configuration-parameters-1 "Direct link to Configuration Parameters")

| Parameter Name | type | Description |
| --- | --- | --- |
| user\_id | string | The user login ID |
| password | string | The user login Password |
| SBC\_URI | url | - Asia - sbcsg.telecmi.com<br>- Europe - sbcuk.telecmi.com<br>- America - sbcus.telecmi.com<br>- India - sbcind.telecmi.com |

### Make call [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#make-call "Direct link to Make call")

Using this method user can able to make call to PSTN or Other user extension.

```javascript
piopiy.call('PHONE_NUMBER');
```

#### Configuration Parameters [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#configuration-parameters-2 "Direct link to Configuration Parameters")

| Parameter Name | type | Description |
| --- | --- | --- |
| PHONE\_NUMBER | string | Enter phone number or user extension number, Phone number start with country code example '13158050050' |

### Make call with Extra params [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#make-call-with-extra-params "Direct link to Make call with Extra params")

Using this method user can able to make call to PSTN or Other user extension with added extra parameters.

```javascript
piopiy.call('PHONE_NUMBER', { extra_param: 'lead' });
```

#### Configuration Parameters [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#configuration-parameters-3 "Direct link to Configuration Parameters")

| Parameter Name | type | Description |
| --- | --- | --- |
| PHONE\_NUMBER | string | Enter phone number or user extension number, Phone number start with country code example '13158050050' |
| extra\_param | JSON object | An optional object containing additional parameters for the call. |

### Get Call Id [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#get-call-id "Direct link to Get Call Id")

Using this method, user's call\_id information can be retrieved.

```javascript
piopiy.getCallId();
```

### Transfer call [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#transfer-call "Direct link to Transfer call")

Using this method, users can transfer the call to a user extension number or phone number with a country code.

```javascript
piopiy.transfer('USER_EXTENSION_NUMBER OR PHONE_NUMBER');
```

#### Configuration Parameters [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#configuration-parameters-4 "Direct link to Configuration Parameters")

| Parameter Name | type | Description |
| --- | --- | --- |
| USER\_EXTENSION\_NUMBER OR PHONE\_NUMBER | string | Enter phone number or user extension number ,Phone number start with country code example '13158050050' |

### Merge Call [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#merge-call "Direct link to Merge Call")

Using this method, the user can merge the transferred call.

```js
piopiy.merge();
```

### Cancel Call [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#cancel-call "Direct link to Cancel Call")

Using this method, user can cancel transfer call.

```js
piopiy.cancel();
```

### Send DTMF [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#send-dtmf "Direct link to Send DTMF")

Using this method user can able to send DTMF tone to ongoing call.

```js
piopiy.sendDtmf('DTMF_TONE');
```

#### Configuration Parameters [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#configuration-parameters-5 "Direct link to Configuration Parameters")

| Parameter Name | type | Description |
| --- | --- | --- |
| DTMF\_TONE | string | Your DTMF tone input |

### Hold Call [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#hold-call "Direct link to Hold Call")

Using this method user can able to hold ongoing call.

```js
piopiy.hold();
```

### Unhold Call [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#unhold-call "Direct link to Unhold Call")

Using this method user can able to unhold ongoing call.

```js
piopiy.unHold();
```

### Mute Call [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#mute-call "Direct link to Mute Call")

Using this method user can able to mute ongoing call.

```js
piopiy.mute();
```

### Unmute Call [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#unmute-call "Direct link to Unmute Call")

Using this method user can able to unmute ongoing call.

```js
piopiy.unMute();
```

### Answer call [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#answer-call "Direct link to Answer call")

Using this method user can able to answer incoming call.

```js
piopiy.answer();
```

### Reject call [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#reject-call "Direct link to Reject call")

Using this method user can able to reject or disconnect incoming call.

```js
piopiy.reject();
```

### Hangup call [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#hangup-call "Direct link to Hangup call")

Using this method user can able to hangup ongoing call.

```js
piopiy.terminate();
```

### Logout [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#logout "Direct link to Logout")

Using this method user can able to logout from SBC session.

```js
piopiy.logout();
```

## PIOPIY Call Event Handler [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#piopiy-call-event-handler "Direct link to PIOPIY Call Event Handler")

### Login [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#login-1 "Direct link to Login")

This event will triger when user login sucessfully

```js
piopiy.on( 'login', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example "Direct link to Example")

```js
piopiy.on( 'login', function ( object ) {

    if(object.code == 200) {

        //  Login successfully and do your stuff here.

    }
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status "Direct link to List of event and status")

| code | status |
| --- | --- |
| 200 | Login Successfully |

### LoginFailed [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#loginfailed "Direct link to LoginFailed")

This event will trigger when user authentication failed.

```js
piopiy.on( 'loginFailed', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example-1 "Direct link to Example")

```js
 piopiy.on( 'loginFailed', function ( object ) {

    if(object.code == 401) {

        //  Verify that the user_id and password are correct.
    }
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status-1 "Direct link to List of event and status")

| code | status |
| --- | --- |
| 401 | invalid user |

### Trying [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#trying "Direct link to Trying")

This event will trigger when user make call to phone number or extension (Destination Number)

```js
piopiy.on( 'trying', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example-2 "Direct link to Example")

```js
piopiy.on( 'trying', function ( object ) {

    if(object.code == 100 ) {

        //  The outgoing call is currently being started.
    }
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status-2 "Direct link to List of event and status")

| code | status | type |
| --- | --- | --- |
| 100 | trying | outgoing |

### Ringing [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#ringing "Direct link to Ringing")

This event will trigger when call start ringing.

```js
piopiy.on( 'ringing', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example-3 "Direct link to Example")

```js
piopiy.on( 'ringing', function ( object ) {

    if(object.code == 183) {

        // An incoming or outgoing call is ringing.
    }
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status-3 "Direct link to List of event and status")

| code | status | type |
| --- | --- | --- |
| 183 | ringing | outgoing & incoming |

### Answered [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#answered "Direct link to Answered")

This event will trigger when ongoing call was answered.

```js
piopiy.on( 'answered', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example-4 "Direct link to Example")

```js
piopiy.on( 'answered', function ( object ) {

    if(object.code == 200) {

        // An incoming or outgoing call is answered.
    }
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status-4 "Direct link to List of event and status")

| code | status |
| --- | --- |
| 200 | answered |

### CallStream [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#callstream "Direct link to CallStream")

This event will trigger when mediastream established.

```js
piopiy.on( 'callStream', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example-5 "Direct link to Example")

```js
piopiy.on( 'callStream', function ( object ) {

    // MediaStream has been established.
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status-5 "Direct link to List of event and status")

| code | stream |
| --- | --- |
| 200 | MediaStream |

### Transfer [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#transfer "Direct link to Transfer")

This event will be triggered when a user transfers a call to a user extension number or a phone number with a country code.

```js
piopiy.on( 'transfer', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example-6 "Direct link to Example")

```js
piopiy.on( 'transfer', function ( object ) {

    if(object.code == 100) {

        // An incoming or outgoing call is transfered.
    }
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status-6 "Direct link to List of event and status")

| code | global | state |
| --- | --- | --- |
| 100 | true | init,started,bridged,ended |

### InComingCall [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#incomingcall "Direct link to InComingCall")

This event will trigger when user recive incmoing call.

```js
piopiy.on( 'inComingCall', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

### Hangup [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#hangup "Direct link to Hangup")

This event will trigger when user reject or hangup incmoing call.

```js
piopiy.on( 'hangup', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example-7 "Direct link to Example")

```js
piopiy.on( 'hangup', function ( object ) {

    if(object.code == 200 ) {

        //  to hangup the incoming and ongoing calls.
    }
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status-7 "Direct link to List of event and status")

| code | status |
| --- | --- |
| 200 | call hangup |

### Ended [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#ended "Direct link to Ended")

This event will trigger when ongoing call end.

```js
piopiy.on( 'ended', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example-8 "Direct link to Example")

```js
piopiy.on( 'ended', function ( object ) {

    if(object.code == 200 ) {

        //  An incoming or outgoing call is ended.
    }
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status-8 "Direct link to List of event and status")

| code | status |
| --- | --- |
| 200 | call ended |
| 408 | Request Timeout |
| 480 | Temporarily Unavailable |
| 484 | Address Incomplete |
| 486 | Busy Here |

### Hold [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#hold "Direct link to Hold")

This event will trigger when ongoing call on hold.

```js
piopiy.on( 'hold', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example-9 "Direct link to Example")

```js
piopiy.on( 'hold', function ( object ) {

    if(object.code == 200 ) {

        //  The call is now being hold.
    }
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status-9 "Direct link to List of event and status")

| code | status | whom |
| --- | --- | --- |
| 200 | call on hold | myself |

### UnHold [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#unhold "Direct link to UnHold")

This event will trigger when ongoing call on unhold.

```js
piopiy.on( 'unhold', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example-10 "Direct link to Example")

```js
piopiy.on( 'unhold', function ( object ) {

    if(object.code == 200 ) {

        //  The call is now being released.
    }
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status-10 "Direct link to List of event and status")

| code | status | whom |
| --- | --- | --- |
| 200 | call on active | myself |

### RTCStats [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#rtcstats "Direct link to RTCStats")

This event will trigger when user RTCStats .

```js
piopiy.on( 'RTCStats', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example-11 "Direct link to Example")

```js
piopiy.on( 'RTCStats', function ( object ) {

    if(object.codec == “audio/PCMU” ) {

        //  The user logged out successfully.
    }
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status-11 "Direct link to List of event and status")

| Paratameter | Value |
| --- | --- |
| codec | “audio/PCMU” |
| delay | 0.042 |
| fractionLost | 0 |
| jitter | 0.000125 |
| network | “wifi” |
| packetLostRate | 0 |
| rountTrip | 0.07360799999999999 |
| totalPacketLost | 1 |

### Error [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#error "Direct link to Error")

This event will trigger when error will occurr.

```js
piopiy.on( 'error', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example-12 "Direct link to Example")

```js
piopiy.on( 'error', function ( object ) {

    if(object.code == 1001 || object.code == 1002) {

        //  If there are any incorrect commands in the function, displays error.
    }
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status-12 "Direct link to List of event and status")

| code | status |
| --- | --- |
| 1001 & 1002 | common error |

### Logout [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#logout-1 "Direct link to Logout")

This event will trigger when user logout .

```js
piopiy.on( 'logout', function ( object ) {

    //  Data is JSON it contain event and status.
});
```

#### Example [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#example-13 "Direct link to Example")

```js
piopiy.on( 'logout', function ( object ) {

    if(object.code == 200 ) {

        //  The user logged out successfully.
    }
});
```

#### List of event and status [​](https://doc.telecmi.com/chub/docs/browser-sdk/\#list-of-event-and-status-13 "Direct link to List of event and status")

| code | status |
| --- | --- |
| 200 | logout successfully |

[Previous\\
\\
AI Streaming](https://doc.telecmi.com/chub/docs/streaming) [Next\\
\\
Voice Feed SDK](https://doc.telecmi.com/chub/docs/voice-feed-sdk)

- [PIOPIY Client JS SDK for voice](https://doc.telecmi.com/chub/docs/browser-sdk/#piopiy-client-js-sdk-for-voice)
- [Package Installation](https://doc.telecmi.com/chub/docs/browser-sdk/#package-installation)
  - [Using NPM](https://doc.telecmi.com/chub/docs/browser-sdk/#using-npm)
  - [Using YARN](https://doc.telecmi.com/chub/docs/browser-sdk/#using-yarn)
- [Monolithic Import](https://doc.telecmi.com/chub/docs/browser-sdk/#monolithic-import)
  - [In Browser](https://doc.telecmi.com/chub/docs/browser-sdk/#in-browser)
  - [In ESM/Typescript](https://doc.telecmi.com/chub/docs/browser-sdk/#in-esmtypescript)
  - [In CommonJS](https://doc.telecmi.com/chub/docs/browser-sdk/#in-commonjs)
- [Get Started](https://doc.telecmi.com/chub/docs/browser-sdk/#get-started)
  - [Initializing the PIOPIY SDK Object](https://doc.telecmi.com/chub/docs/browser-sdk/#initializing-the-piopiy-sdk-object)
- [Configuration Parameters](https://doc.telecmi.com/chub/docs/browser-sdk/#configuration-parameters)
- [PIOPIY Methods](https://doc.telecmi.com/chub/docs/browser-sdk/#piopiy-methods)
  - [Login](https://doc.telecmi.com/chub/docs/browser-sdk/#login)
  - [Make call](https://doc.telecmi.com/chub/docs/browser-sdk/#make-call)
  - [Make call with Extra params](https://doc.telecmi.com/chub/docs/browser-sdk/#make-call-with-extra-params)
  - [Get Call Id](https://doc.telecmi.com/chub/docs/browser-sdk/#get-call-id)
  - [Transfer call](https://doc.telecmi.com/chub/docs/browser-sdk/#transfer-call)
  - [Merge Call](https://doc.telecmi.com/chub/docs/browser-sdk/#merge-call)
  - [Cancel Call](https://doc.telecmi.com/chub/docs/browser-sdk/#cancel-call)
  - [Send DTMF](https://doc.telecmi.com/chub/docs/browser-sdk/#send-dtmf)
  - [Hold Call](https://doc.telecmi.com/chub/docs/browser-sdk/#hold-call)
  - [Unhold Call](https://doc.telecmi.com/chub/docs/browser-sdk/#unhold-call)
  - [Mute Call](https://doc.telecmi.com/chub/docs/browser-sdk/#mute-call)
  - [Unmute Call](https://doc.telecmi.com/chub/docs/browser-sdk/#unmute-call)
  - [Answer call](https://doc.telecmi.com/chub/docs/browser-sdk/#answer-call)
  - [Reject call](https://doc.telecmi.com/chub/docs/browser-sdk/#reject-call)
  - [Hangup call](https://doc.telecmi.com/chub/docs/browser-sdk/#hangup-call)
  - [Logout](https://doc.telecmi.com/chub/docs/browser-sdk/#logout)
- [PIOPIY Call Event Handler](https://doc.telecmi.com/chub/docs/browser-sdk/#piopiy-call-event-handler)
  - [Login](https://doc.telecmi.com/chub/docs/browser-sdk/#login-1)
  - [LoginFailed](https://doc.telecmi.com/chub/docs/browser-sdk/#loginfailed)
  - [Trying](https://doc.telecmi.com/chub/docs/browser-sdk/#trying)
  - [Ringing](https://doc.telecmi.com/chub/docs/browser-sdk/#ringing)
  - [Answered](https://doc.telecmi.com/chub/docs/browser-sdk/#answered)
  - [CallStream](https://doc.telecmi.com/chub/docs/browser-sdk/#callstream)
  - [Transfer](https://doc.telecmi.com/chub/docs/browser-sdk/#transfer)
  - [InComingCall](https://doc.telecmi.com/chub/docs/browser-sdk/#incomingcall)
  - [Hangup](https://doc.telecmi.com/chub/docs/browser-sdk/#hangup)
  - [Ended](https://doc.telecmi.com/chub/docs/browser-sdk/#ended)
  - [Hold](https://doc.telecmi.com/chub/docs/browser-sdk/#hold)
  - [UnHold](https://doc.telecmi.com/chub/docs/browser-sdk/#unhold)
  - [RTCStats](https://doc.telecmi.com/chub/docs/browser-sdk/#rtcstats)
  - [Error](https://doc.telecmi.com/chub/docs/browser-sdk/#error)
  - [Logout](https://doc.telecmi.com/chub/docs/browser-sdk/#logout-1)