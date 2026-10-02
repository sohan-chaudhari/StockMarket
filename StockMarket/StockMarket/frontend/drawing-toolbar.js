/**
 * drawing-toolbar.js - Interactive Drawing Toolbar & Draggable Floating Favorites Bar
 * Features:
 * - Nested tool families, dynamic sidebar icon swaps, flyout submenus with stars
 * - Standalone draggable floating favorites bar on desktop
 * - Seamless mobile starred tool buttons filling empty toolbar space
 * - Smooth touch scrolling on mobile without accidental submenu closure
 * - WhatsApp & TradingView style categorized Emoji Picker with live search
 * - Behind-candles rendering integration for emojis and stickers
 */

(function () {
  'use strict';

  const { ICONS, TOOLBAR_FAMILIES, ALL_TOOLS_MAP, store } = window.ToolbarConfig;

  // Add Close icon to ICONS if not present
  ICONS.close = `<svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6L18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;

  // Default selected emoji
  window.selectedEmoji = window.selectedEmoji || '📈';

  // Comprehensive WhatsApp / TradingView Style Emojis Database
  const EMOJI_CATEGORIES = [
    {
      id: 'trading',
      name: 'Trading & Finance',
      icon: '📈',
      emojis: [
        { char: '📈', name: 'chart increasing upward trend rally bullish' },
        { char: '📉', name: 'chart decreasing downward trend dump bearish' },
        { char: '🚀', name: 'rocket moon pump breakout ath' },
        { char: '💰', name: 'money bag profit bags rich wealth' },
        { char: '💵', name: 'dollar bill cash currency usd' },
        { char: '💸', name: 'money with wings flying loss fees' },
        { char: '💎', name: 'diamond hands value luxury holding' },
        { char: '🐂', name: 'bull bullish market rally long' },
        { char: '🐻', name: 'bear bearish dump market short' },
        { char: '🎯', name: 'bullseye target goal tp take profit' },
        { char: '📊', name: 'bar chart statistics volume histogram' },
        { char: '⚡', name: 'zap lightning speed impulse break' },
        { char: '🔔', name: 'bell alert notification trigger' },
        { char: '🔥', name: 'fire hot trend breakout momentum' },
        { char: '🏆', name: 'trophy winner gain success top' },
        { char: '💯', name: 'hundred percent perfect score max' },
        { char: '⭐', name: 'star gold favorite rating key' },
        { char: '🌟', name: 'glowing star bright shining' },
        { char: '💥', name: 'collision explosion dump crash vol' },
        { char: '🧠', name: 'brain smart analysis strategy mental' },
        { char: '💡', name: 'light bulb idea analysis eureka' },
        { char: '📌', name: 'pushpin key level pivot marker' },
        { char: '🟢', name: 'green circle buy entry call long bull' },
        { char: '🔴', name: 'red circle sell exit put short bear' },
        { char: '🔺', name: 'red triangle up higher high resistance' },
        { char: '🔻', name: 'red triangle down lower low support' },
        { char: '✅', name: 'check mark confirmed hit target passed' },
        { char: '❌', name: 'cross mark stoploss failed invalid sl' },
        { char: '⚠️', name: 'warning danger risk caution alert' },
        { char: '⛔', name: 'no entry stop resistant limit' },
        { char: '💲', name: 'heavy dollar sign price money' },
        { char: '💹', name: 'chart increasing with yen market rise' },
        { char: '🏛️', name: 'classical building bank fed institution sec' },
        { char: '🏦', name: 'bank money central deposit rbi' }
      ]
    },
    {
      id: 'smileys',
      name: 'Smileys & Emotion',
      icon: '😀',
      emojis: [
        { char: '😀', name: 'grinning face happy smile' },
        { char: '😃', name: 'grinning face big eyes' },
        { char: '😄', name: 'grinning face smiling eyes' },
        { char: '😁', name: 'beaming face smiling eyes win' },
        { char: '😆', name: 'grinning squinting face laugh' },
        { char: '😅', name: 'grinning face sweat relief phew' },
        { char: '🤣', name: 'rolling on the floor laughing rofl' },
        { char: '😂', name: 'face with tears of joy lol' },
        { char: '🙂', name: 'slightly smiling face ok' },
        { char: '🙃', name: 'upside-down face sarcasm' },
        { char: '😉', name: 'winking face wink' },
        { char: '😊', name: 'smiling face with smiling eyes warm' },
        { char: '😇', name: 'smiling face with halo angel good' },
        { char: '🥰', name: 'smiling face with hearts love adore' },
        { char: '😍', name: 'heart eyes love enamored' },
        { char: '🤩', name: 'star-struck excited wow' },
        { char: '😘', name: 'face blowing a kiss love' },
        { char: '😋', name: 'face savoring food delicious' },
        { char: '😛', name: 'face with tongue playful' },
        { char: '😜', name: 'winking face with tongue crazy' },
        { char: '🤪', name: 'zany face wild crazy' },
        { char: '🤑', name: 'money-mouth face rich profit gain' },
        { char: '🤗', name: 'smiling face with open hands hug' },
        { char: '🤫', name: 'shushing face quiet secret insider' },
        { char: '🤔', name: 'thinking face hmm wonder speculate' },
        { char: '🤐', name: 'zipper-mouth face silent' },
        { char: '🤨', name: 'face with raised eyebrow skeptical doubt' },
        { char: '😐', name: 'neutral face flat sideways' },
        { char: '😑', name: 'expressionless face blank' },
        { char: '😶', name: 'face without mouth silence mute' },
        { char: '😏', name: 'smirking face sly smart' },
        { char: '😒', name: 'unamused face unimpressed boring' },
        { char: '🙄', name: 'face with rolling eyes whatever' },
        { char: '😬', name: 'grimacing face nervous tight' },
        { char: '🤥', name: 'lying face fake news fraud' },
        { char: '😌', name: 'relieved face calm serene' },
        { char: '😔', name: 'pensive face sad regret' },
        { char: '😪', name: 'sleepy face tired' },
        { char: '🤤', name: 'drooling face want' },
        { char: '😴', name: 'sleeping face sleep market closed' },
        { char: '😷', name: 'face with medical mask' },
        { char: '🤒', name: 'face with thermometer sick' },
        { char: '🤕', name: 'face with head-bandage hurt loss' },
        { char: '🤢', name: 'nauseated face sick disgusted' },
        { char: '🤮', name: 'face vomiting puking dump red' },
        { char: '🥵', name: 'hot face overheated volatile' },
        { char: '🥶', name: 'cold face freezing market winter' },
        { char: '🥴', name: 'woozy face dizzy drunk' },
        { char: '😵', name: 'dizzy face shocked wiped out' },
        { char: '🤯', name: 'exploding head mind blown shock wow' },
        { char: '🤠', name: 'cowboy hat face wild' },
        { char: '🥳', name: 'partying face celebrate profit win' },
        { char: '😎', name: 'smiling face with sunglasses cool pro trader' },
        { char: '🤓', name: 'nerd face smart quant math' },
        { char: '🧐', name: 'face with monocle inspect dyor' },
        { char: '😕', name: 'confused face mixed signals' },
        { char: '😟', name: 'worried face fear uncertainty' },
        { char: '🙁', name: 'slightly frowning face loss' },
        { char: '😮', name: 'face with open mouth surprised gap' },
        { char: '😲', name: 'astonished face shock' },
        { char: '😳', name: 'flushed face shocked caught' },
        { char: '🥺', name: 'pleading face please hold' },
        { char: '😦', name: 'frowning face open mouth drop' },
        { char: '😨', name: 'fearful face scared panic' },
        { char: '😰', name: 'anxious face with sweat margin call' },
        { char: '😥', name: 'sad but relieved face close call' },
        { char: '😢', name: 'crying face tear sad' },
        { char: '😭', name: 'loudly crying face loss rekt liquid' },
        { char: '😱', name: 'face screaming in fear panic dump' },
        { char: '😖', name: 'confounded face' },
        { char: '😣', name: 'persevering face' },
        { char: '😞', name: 'disappointed face red' },
        { char: '😓', name: 'downcast face with sweat stress' },
        { char: '😩', name: 'weary face exhausted' },
        { char: '😫', name: 'tired face sleepless' },
        { char: '🥱', name: 'yawning face boring sideways range' },
        { char: '😤', name: 'face with steam from nose angry' },
        { char: '😡', name: 'enraged face mad market manipulation' },
        { char: '😠', name: 'angry face furious' },
        { char: '🤬', name: 'face with symbols on mouth swear' },
        { char: '😈', name: 'smiling face with horns devil evil dump' },
        { char: '👿', name: 'angry face with horns wicked' },
        { char: '💀', name: 'skull dead rekt liquidated zero' },
        { char: '☠️', name: 'skull and crossbones fatal danger' },
        { char: '💩', name: 'pile of poo trash shitcoin' },
        { char: '🤡', name: 'clown face fool bull trap bear trap' },
        { char: '👹', name: 'ogre monster' },
        { char: '👺', name: 'goblin' },
        { char: '👻', name: 'ghost phantom order' },
        { char: '👽', name: 'alien ufo smart money' },
        { char: '👾', name: 'alien monster game' },
        { char: '🤖', name: 'robot bot algo automated trading' }
      ]
    },
    {
      id: 'gestures',
      name: 'Gestures & People',
      icon: '👍',
      emojis: [
        { char: '👍', name: 'thumbs up agree approve good buy' },
        { char: '👎', name: 'thumbs down bad disapprove sell' },
        { char: '👌', name: 'ok hand perfect zero level' },
        { char: '🤌', name: 'pinched fingers what how why' },
        { char: '🤏', name: 'pinching hand small tiny pip' },
        { char: '✌️', name: 'victory hand peace win' },
        { char: '🤞', name: 'crossed fingers luck hope pray' },
        { char: '🤟', name: 'love-you gesture' },
        { char: '🤘', name: 'sign of the horns rock bull horns' },
        { char: '🤙', name: 'call me hand options call' },
        { char: '👈', name: 'backhand index pointing left previous' },
        { char: '👉', name: 'backhand index pointing right next' },
        { char: '👆', name: 'backhand index pointing up target' },
        { char: '👇', name: 'backhand index pointing down support' },
        { char: '☝️', name: 'index pointing up one first' },
        { char: '✋', name: 'raised hand stop halt' },
        { char: '🤚', name: 'raised back of hand' },
        { char: '🖐️', name: 'hand with fingers splayed 5' },
        { char: '🖖', name: 'vulcan salute long live' },
        { char: '👋', name: 'waving hand hello bye exit' },
        { char: '🤝', name: 'handshake deal agreement settlement' },
        { char: '🙏', name: 'folded hands pray please hope' },
        { char: '👏', name: 'clapping hands applause profit' },
        { char: '🙌', name: 'raising hands celebrate ath' },
        { char: '👐', name: 'open hands spread' },
        { char: '🤲', name: 'palms up together' },
        { char: '✊', name: 'raised fist power hodl hold' },
        { char: '👊', name: 'oncoming fist punch strike' },
        { char: '🤛', name: 'left-facing fist' },
        { char: '🤜', name: 'right-facing fist' },
        { char: '💪', name: 'flexed biceps strong strength momentum' },
        { char: '🦾', name: 'mechanical arm bot' },
        { char: '🦿', name: 'mechanical leg' },
        { char: '👂', name: 'ear listen news whisper' },
        { char: '👀', name: 'eyes look watching see monitor' },
        { char: '👁️', name: 'eye watching see vision' }
      ]
    },
    {
      id: 'animals',
      name: 'Animals & Nature',
      icon: '🐂',
      emojis: [
        { char: '🐂', name: 'bull market buyer green pump' },
        { char: '🐃', name: 'water buffalo' },
        { char: '🐄', name: 'cow cash cow' },
        { char: '🐻', name: 'bear market seller red dump' },
        { char: '🐼', name: 'panda' },
        { char: '🐨', name: 'koala' },
        { char: '🦁', name: 'lion king apex strong' },
        { char: '🐯', name: 'tiger aggressive' },
        { char: '🐆', name: 'leopard fast agile' },
        { char: '🐺', name: 'wolf pack lone wolf wall street' },
        { char: '🦊', name: 'fox clever sly smart' },
        { char: '🦝', name: 'raccoon' },
        { char: '🐱', name: 'cat dead cat bounce' },
        { char: '🐶', name: 'dog doge meme' },
        { char: '🐵', name: 'monkey ape diamond ape' },
        { char: '🦍', name: 'gorilla ape strong apes together' },
        { char: '🦄', name: 'unicorn billion startup valuation' },
        { char: '🐴', name: 'horse race winner' },
        { char: '🐗', name: 'boar pig greed' },
        { char: '🦅', name: 'eagle freedom hawk hawkish fed' },
        { char: '🦆', name: 'duck sitting duck target' },
        { char: '🦉', name: 'owl wise wisdom guru' },
        { char: '🦇', name: 'bat pattern harmonic' },
        { char: '🐝', name: 'honeybee busy work' },
        { char: '🐛', name: 'bug software glitch issue' },
        { char: '🦋', name: 'butterfly pattern harmonic' },
        { char: '🐌', name: 'snail slow market consolidate range' },
        { char: '🐞', name: 'lady beetle luck' },
        { char: '🐜', name: 'ant worker small' },
        { char: '🕷️', name: 'spider web network' },
        { char: '🦂', name: 'scorpion sting' },
        { char: '🐢', name: 'turtle slow steady compounding' },
        { char: '🐍', name: 'snake trap fakeout' },
        { char: '🦖', name: 't-rex dino old dinosaur legacy' },
        { char: '🐙', name: 'octopus tentacle multi' },
        { char: '🦐', name: 'shrimp retail small fish' },
        { char: '🦀', name: 'crab crab pattern sideways crab' },
        { char: '🐡', name: 'blowfish inflate bubble' },
        { char: '🐬', name: 'dolphin smart player' },
        { char: '🐳', name: 'whale institution big player block' },
        { char: '🦈', name: 'shark pattern predatory liquidity' },
        { char: '🌲', name: 'evergreen tree green' },
        { char: '🌳', name: 'deciduous tree growth' },
        { char: '🌴', name: 'palm tree beach retirement vacation' },
        { char: '🌱', name: 'seedling growth nascent early' },
        { char: '🍀', name: 'four leaf clover luck fortune jackpot' },
        { char: '🍄', name: 'mushroom boom' },
        { char: '🌞', name: 'sun with face bright future' },
        { char: '🌕', name: 'full moon pump cycle' },
        { char: '🌙', name: 'crescent moon night' },
        { char: '🪐', name: 'ringed planet saturn outer space' },
        { char: '💫', name: 'dizzy star spark twinkle' },
        { char: '⚡', name: 'high voltage electricity power fast' },
        { char: '💥', name: 'boom explosion breakout' },
        { char: '🔥', name: 'fire flame lit hot trend' },
        { char: '🌪️', name: 'tornado volatility chaos storm' },
        { char: '🌈', name: 'rainbow upside recovery' },
        { char: '☀️', name: 'sun bright day open' },
        { char: '⛅', name: 'sun behind cloud mixed' },
        { char: '🌧️', name: 'cloud with rain dump drop down' },
        { char: '🌩️', name: 'cloud with lightning crash dump' },
        { char: '❄️', name: 'snowflake winter crypto winter bear' },
        { char: '🌊', name: 'water wave elliott wave tidal surge' }
      ]
    },
    {
      id: 'objects',
      name: 'Objects & Tools',
      icon: '💼',
      emojis: [
        { char: '📱', name: 'mobile phone app mobile view' },
        { char: '💻', name: 'laptop computer terminal setup' },
        { char: '🖥️', name: 'desktop computer trading screen monitors' },
        { char: '🖨️', name: 'printer money printer brrr' },
        { char: '⌨️', name: 'keyboard shortcuts typing' },
        { char: '🖱️', name: 'mouse pointer click' },
        { char: '🔍', name: 'magnifying glass search find zoom in' },
        { char: '🔎', name: 'magnifying glass tilted right' },
        { char: '🕯️', name: 'candle candlestick bar' },
        { char: '💡', name: 'light bulb idea eureka strategy' },
        { char: '🔦', name: 'flashlight discover' },
        { char: '📔', name: 'notebook trading journal diary' },
        { char: '📕', name: 'closed book red ledger' },
        { char: '📗', name: 'green book green ledger profit' },
        { char: '📚', name: 'books knowledge study reading' },
        { char: '📜', name: 'scroll smart contract terms' },
        { char: '📄', name: 'page document whitepaper statement' },
        { char: '📰', name: 'newspaper headlines news sentiment' },
        { char: '📑', name: 'bookmark tabs watchlist' },
        { char: '🔖', name: 'bookmark save level' },
        { char: '🏷️', name: 'label tag ticker price tag' },
        { char: '💰', name: 'money bag rich bags profit' },
        { char: '🪙', name: 'coin crypto token gold asset' },
        { char: '💵', name: 'dollar banknote usd cash' },
        { char: '💶', name: 'euro banknote eur' },
        { char: '💷', name: 'pound banknote gbp' },
        { char: '💸', name: 'money with wings cash flying fees' },
        { char: '💳', name: 'credit card payment fiat' },
        { char: '🧾', name: 'receipt bill transaction pnl' },
        { char: '✉️', name: 'envelope message mail alert' },
        { char: '📦', name: 'package box delivery order fill' },
        { char: '📝', name: 'memo notes write trade plan' },
        { char: '💼', name: 'briefcase portfolio assets work' },
        { char: '📁', name: 'file folder watchlist category' },
        { char: '📂', name: 'open file folder' },
        { char: '🗂️', name: 'card index dividers' },
        { char: '📅', name: 'calendar date earnings expiry session' },
        { char: '📈', name: 'chart increasing uptrend' },
        { char: '📉', name: 'chart decreasing downtrend' },
        { char: '📊', name: 'bar chart histogram volume depth' },
        { char: '📋', name: 'clipboard copy paste order' },
        { char: '📌', name: 'pushpin pin level key price' },
        { char: '📍', name: 'round pushpin location anchor mark' },
        { char: '📎', name: 'paperclip attach link' },
        { char: '📏', name: 'straight ruler measure distance target' },
        { char: '📐', name: 'triangular ruler angle trend pitchfork' },
        { char: '✂️', name: 'scissors cut stop loss cut' },
        { char: '🗑️', name: 'wastebasket delete clear trash' },
        { char: '🔒', name: 'locked lock protect secure safe' },
        { char: '🔓', name: 'unlocked open free access' },
        { char: '🔑', name: 'key pivot key resistance break' },
        { char: '🗝️', name: 'old key secret alpha' },
        { char: '🔨', name: 'hammer candlestick pattern hammer' },
        { char: '🪓', name: 'axe chop market' },
        { char: '⛏️', name: 'pickaxe mine mining hash' },
        { char: '🛠️', name: 'hammer and wrench settings dev build' },
        { char: '⚔️', name: 'crossed swords fight battle bulls bears' },
        { char: '💣', name: 'bomb explosion high risk liquidation' },
        { char: '🏹', name: 'bow and arrow target hit accuracy' },
        { char: '🛡️', name: 'shield hedge protection defense risk' },
        { char: '🔧', name: 'wrench fix modify tool' },
        { char: '⚙️', name: 'gear settings engine parameters' },
        { char: '⚖️', name: 'scales balance risk reward ratio' },
        { char: '🔗', name: 'link chain blockchain on-chain' },
        { char: '🧲', name: 'magnet snap attraction magnet tool' },
        { char: '🪜', name: 'ladder climb steps laddering orders' },
        { char: '🧪', name: 'test tube experiment backtest strategy' },
        { char: '🔬', name: 'microscope analysis examine zoom deep' },
        { char: '🔭', name: 'telescope lookahead forecast outlook' },
        { char: '📡', name: 'satellite antenna live data feed ws' },
        { char: '🚪', name: 'door exit entry point gate' },
        { char: '🪟', name: 'window opportunity gap gap-fill' },
        { char: '🛒', name: 'shopping cart buy order basket' },
        { char: '⏰', name: 'alarm clock timer session alert' },
        { char: '⏱️', name: 'stopwatch speed execution' },
        { char: '⏳', name: 'hourglass not done waiting pending' },
        { char: '⌛', name: 'hourglass done time expiry filled' }
      ]
    },
    {
      id: 'symbols',
      name: 'Symbols & Shapes',
      icon: '❤️',
      emojis: [
        { char: '❤️', name: 'red heart love like favorite' },
        { char: '💚', name: 'green heart profit up green' },
        { char: '💙', name: 'blue heart trust' },
        { char: '💛', name: 'yellow heart caution' },
        { char: '💜', name: 'purple heart' },
        { char: '🖤', name: 'black heart dark' },
        { char: '🤍', name: 'white heart' },
        { char: '💔', name: 'broken heart loss drawdown dump' },
        { char: '❤️‍🔥', name: 'heart on fire burning hot' },
        { char: '✨', name: 'sparkles clean magic alpha pristine' },
        { char: '⭐', name: 'star star rating favorite bookmark' },
        { char: '🌟', name: 'glowing star shine winner' },
        { char: '💥', name: 'collision boom breakout pump' },
        { char: '🔥', name: 'fire hot trending momentum' },
        { char: '⚠️', name: 'warning danger risk caution caution' },
        { char: '⛔', name: 'no entry stop resistant forbidden limit' },
        { char: '🛑', name: 'stop sign stop loss exit halt' },
        { char: '🚫', name: 'prohibited no access ban' },
        { char: '✅', name: 'check mark button verified target hit pass' },
        { char: '❌', name: 'cross mark cancel error fail stop' },
        { char: '❓', name: 'question mark doubt uncertain question' },
        { char: '❗', name: 'exclamation mark important urgent alert' },
        { char: '‼️', name: 'double exclamation mark high volatility' },
        { char: '⁉️', name: 'exclamation question mark shock' },
        { char: '💯', name: 'hundred points top score full size 100' },
        { char: '💲', name: 'dollar sign currency usd fiat' },
        { char: '💹', name: 'chart increasing with yen rise profit' },
        { char: '🟢', name: 'green circle call long buy bull' },
        { char: '🔴', name: 'red circle put short sell bear' },
        { char: '🟡', name: 'yellow circle wait neutral sideways' },
        { char: '🔵', name: 'blue circle support info marker' },
        { char: '🟣', name: 'purple circle' },
        { char: '🟠', name: 'orange circle alert' },
        { char: '⚫', name: 'black circle' },
        { char: '⚪', name: 'white circle' },
        { char: '🟩', name: 'green square green box buy zone demand' },
        { char: '🟥', name: 'red square red box sell zone supply' },
        { char: '🟨', name: 'yellow square consolidation range box' },
        { char: '🟦', name: 'blue square order block support' },
        { char: '🟧', name: 'orange square' },
        { char: '🔺', name: 'red triangle pointed up higher high target' },
        { char: '🔻', name: 'red triangle pointed down lower low target' },
        { char: '🔼', name: 'upwards button pump up bullish' },
        { char: '🔽', name: 'downwards button dump down bearish' },
        { char: '⬆️', name: 'up arrow bullish breakout climb' },
        { char: '⬇️', name: 'down arrow bearish breakdown drop' },
        { char: '➡️', name: 'right arrow sideways channel range' },
        { char: '⬅️', name: 'left arrow back historical' },
        { char: '↗️', name: 'up-right arrow uptrend channel higher' },
        { char: '↘️', name: 'down-right arrow downtrend channel lower' },
        { char: '🔄', name: 'counterclockwise arrows retest reversal flip' },
        { char: '🔁', name: 'repeat button cycle range loop pattern' },
        { char: '🔀', name: 'shuffle tracks button chop whipsaw random' },
        { char: '0️⃣', name: 'digit zero' },
        { char: '1️⃣', name: 'digit one wave 1 first' },
        { char: '2️⃣', name: 'digit two wave 2 second' },
        { char: '3️⃣', name: 'digit three wave 3 impulse' },
        { char: '4️⃣', name: 'digit four wave 4 fourth' },
        { char: '5️⃣', name: 'digit five wave 5 fifth' },
        { char: '🔟', name: 'ten 10x leverage 10' },
        { char: '➕', name: 'plus add position size scale in' },
        { char: '➖', name: 'minus reduce size scale out' },
        { char: '✖️', name: 'multiply leverage mult cross' },
        { char: '➗', name: 'division split rebalance' },
        { char: '🟰', name: 'heavy equals sign balance equilibrium' },
        { char: '♾️', name: 'infinity infinite gains unlimited' },
        { char: '🆗', name: 'ok button confirm validate' },
        { char: '🆙', name: 'up button pump higher' },
        { char: '🆕', name: 'new button new trade order' },
        { char: '🆓', name: 'free button' }
      ]
    },
    {
      id: 'flags',
      name: 'Flags',
      icon: '🏁',
      emojis: [
        { char: '🇮🇳', name: 'flag india nse bse nifty sensex inr' },
        { char: '🇺🇸', name: 'flag united states us dow sp500 nasdaq usd' },
        { char: '🇬🇧', name: 'flag united kingdom ftse gbp' },
        { char: '🇪🇺', name: 'flag european union euro dax eur' },
        { char: '🇯🇵', name: 'flag japan nikkei jpy' },
        { char: '🇩🇪', name: 'flag germany dax eurusd' },
        { char: '🇫🇷', name: 'flag france cac' },
        { char: '🇨🇳', name: 'flag china shanghai cny' },
        { char: '🇦🇺', name: 'flag australia asx aud' },
        { char: '🇨🇦', name: 'flag canada tsx cad' },
        { char: '🇧🇷', name: 'flag brazil ibovespa' },
        { char: '🇸🇦', name: 'flag saudi arabia oil brent' },
        { char: '🇦🇪', name: 'flag united arab emirates dxb' },
        { char: '🇸🇬', name: 'flag singapore sgx nifty' },
        { char: '🇰🇷', name: 'flag south korea kospi' },
        { char: '🇿🇦', name: 'flag south africa' },
        { char: '🏁', name: 'chequered flag finish win target final' },
        { char: '🚩', name: 'triangular flag red flag warning marker' },
        { char: '🎌', name: 'crossed flags' },
        { char: '🏴‍☠️', name: 'pirate flag degen' }
      ]
    }
  ];

  /**
   * Interactive WhatsApp / TradingView Style Emoji Picker Modal
   */
  class EmojiPickerManager {
    constructor(toolbarManager) {
      this.tm = toolbarManager;
      this.pickerEl = null;
      this.activeCatId = 'trading';
      this.searchQuery = '';
      this.onSelectCallback = null;
    }

    init() {
      if (this.pickerEl) return;
      this.pickerEl = document.createElement('div');
      this.pickerEl.id = 'emoji-picker-panel';
      this.pickerEl.className = 'emoji-picker-panel';
      document.body.appendChild(this.pickerEl);

      this._bindEvents();
    }

    open(anchorRect, onSelectCallback) {
      this.init();
      this.searchQuery = '';
      this.activeCatId = 'trading';
      this.onSelectCallback = typeof onSelectCallback === 'function' ? onSelectCallback : null;
      this.render();

      const isMobile = window.innerWidth <= 768;
      this.pickerEl.classList.remove('is-mobile', 'is-desktop');

      if (isMobile) {
        this.pickerEl.classList.add('is-mobile');
        this.pickerEl.style.left = '0';
        this.pickerEl.style.top = 'auto';
        this.pickerEl.style.bottom = '0';
      } else {
        this.pickerEl.classList.add('is-desktop');
        const tbEl = document.getElementById('drawing-toolbar');
        const tbRect = tbEl ? tbEl.getBoundingClientRect() : { right: 54, top: 80 };
        let left = Math.round(tbRect.right + 8);
        let top = anchorRect ? Math.round(anchorRect.top) : 100;
        const pickerH = 460;
        if (top + pickerH > window.innerHeight - 20) {
          top = Math.max(20, window.innerHeight - pickerH - 20);
        }
        this.pickerEl.style.left = `${left}px`;
        this.pickerEl.style.top = `${top}px`;
        this.pickerEl.style.bottom = 'auto';
      }

      this.pickerEl.classList.add('visible');
      const searchInput = this.pickerEl.querySelector('.emoji-search-input');
      if (searchInput && !isMobile) {
        setTimeout(() => searchInput.focus(), 50);
      }
    }

    close() {
      if (this.pickerEl) {
        this.pickerEl.classList.remove('visible');
      }
    }

    isOpen() {
      return this.pickerEl && this.pickerEl.classList.contains('visible');
    }

    render() {
      if (!this.pickerEl) return;

      this.pickerEl.innerHTML = `
        <div class="emoji-picker-header">
          <div class="emoji-picker-title-row">
            <span class="emoji-picker-title">Select Emoji</span>
            <button type="button" class="emoji-picker-close-btn" title="Close">&times;</button>
          </div>
          <div class="emoji-search-wrapper">
            <span class="emoji-search-icon">🔍</span>
            <input type="text" class="emoji-search-input" placeholder="Search emojis (e.g. rocket, bull, star)..." value="${this.searchQuery}">
          </div>
          <div class="emoji-category-tabs">
            ${EMOJI_CATEGORIES.map(cat => `
              <button type="button" class="emoji-cat-btn ${cat.id === this.activeCatId && !this.searchQuery ? 'active' : ''}" data-cat="${cat.id}" title="${cat.name}">
                ${cat.icon}
              </button>
            `).join('')}
          </div>
        </div>
        <div class="emoji-picker-body" id="emoji-picker-body">
          ${this._renderEmojiContent()}
        </div>
      `;

      this._attachDynamicListeners();
    }

    _renderEmojiContent() {
      const q = this.searchQuery.trim().toLowerCase();

      if (q) {
        const matches = [];
        EMOJI_CATEGORIES.forEach(cat => {
          cat.emojis.forEach(e => {
            if (e.char === q || e.name.toLowerCase().includes(q)) {
              matches.push(e);
            }
          });
        });

        if (matches.length === 0) {
          return `<div class="emoji-no-results">No matching emojis found for "${this.searchQuery}"</div>`;
        }

        return `
          <div class="emoji-category-section">
            <div class="emoji-category-title">Search Results (${matches.length})</div>
            <div class="emoji-grid">
              ${matches.map(e => `
                <button type="button" class="emoji-item-btn ${e.char === window.selectedEmoji ? 'selected' : ''}" data-emoji="${e.char}" title="${e.name}">
                  ${e.char}
                </button>
              `).join('')}
            </div>
          </div>
        `;
      }

      // Render Categories
      return EMOJI_CATEGORIES.map(cat => `
        <div class="emoji-category-section" id="emoji-sec-${cat.id}">
          <div class="emoji-category-title">${cat.icon} ${cat.name}</div>
          <div class="emoji-grid">
            ${cat.emojis.map(e => `
              <button type="button" class="emoji-item-btn ${e.char === window.selectedEmoji ? 'selected' : ''}" data-emoji="${e.char}" title="${e.name}">
                ${e.char}
              </button>
            `).join('')}
          </div>
        </div>
      `).join('');
    }

    _bindEvents() {
      // Prevent outside dismissal when touching inside the picker panel
      this.pickerEl.addEventListener('pointerdown', (e) => e.stopPropagation());
      this.pickerEl.addEventListener('click', (e) => e.stopPropagation());
    }

    _attachDynamicListeners() {
      // Close button
      const closeBtn = this.pickerEl.querySelector('.emoji-picker-close-btn');
      if (closeBtn) {
        closeBtn.addEventListener('click', () => this.close());
      }

      // Search input
      const searchInput = this.pickerEl.querySelector('.emoji-search-input');
      if (searchInput) {
        searchInput.addEventListener('input', (e) => {
          this.searchQuery = e.target.value;
          const body = this.pickerEl.querySelector('#emoji-picker-body');
          if (body) {
            body.innerHTML = this._renderEmojiContent();
            this._attachEmojiClickHandlers();
          }
        });
      }

      // Category tab clicks
      const tabBtns = this.pickerEl.querySelectorAll('.emoji-cat-btn');
      tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          const catId = btn.getAttribute('data-cat');
          this.activeCatId = catId;
          this.searchQuery = '';
          const sInput = this.pickerEl.querySelector('.emoji-search-input');
          if (sInput) sInput.value = '';

          tabBtns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');

          const body = this.pickerEl.querySelector('#emoji-picker-body');
          if (body) {
            body.innerHTML = this._renderEmojiContent();
            this._attachEmojiClickHandlers();
            const targetSec = this.pickerEl.querySelector(`#emoji-sec-${catId}`);
            if (targetSec) {
              targetSec.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
          }
        });
      });

      this._attachEmojiClickHandlers();
    }

    _attachEmojiClickHandlers() {
      const emojiBtns = this.pickerEl.querySelectorAll('.emoji-item-btn');
      emojiBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const emojiChar = btn.getAttribute('data-emoji');
          if (!emojiChar) return;

          window.selectedEmoji = emojiChar;
          if (this.onSelectCallback) {
            this.onSelectCallback(emojiChar);
          } else {
            this.tm.onEmojiSelected(emojiChar);
          }
          this.close();
        });
      });
    }
  }

  class DrawingToolbarManager {
    constructor() {
      this.toolbarEl = null;
      this.floatingBarEl = null;
      this.isDraggingFloatingBar = false;
      this.dragOffset = { x: 0, y: 0 };
      this.activeOpenFamilyId = null;
      this.emojiPicker = new EmojiPickerManager(this);

      // Expose rich emoji picker helpers globally
      window.openEmojiPicker = (anchorRect, callback) => {
        this.emojiPicker.open(anchorRect, callback);
      };
      window.showEmojiPickerForStylePanel = (btn) => {
        const rect = btn ? btn.getBoundingClientRect() : null;
        this.emojiPicker.open(rect, (emoji) => {
          if (btn) btn.textContent = emoji;
          if (window.toolManager && window.toolManager.selectedDrawing) {
            const d = window.toolManager.selectedDrawing;
            const engine = window.toolManager.engine;
            const before = d.model ? d.model.toJSON() : null;
            if (!d.content) d.content = {};
            d.content.plain = emoji;
            if (d.model) {
              d.model.content = d.content;
              if (d.syncToModel) d.syncToModel();
            }
            if (d.model && engine) {
              engine.undoManager.pushCommand('restyle', before, d.model.toJSON());
              engine.markDirty();
            } else if (window.toolManager.saveDrawings) {
              window.toolManager.saveDrawings();
            }
            if (d._invalidateCache) d._invalidateCache();
            window.toolManager.redraw();
          }
        });
      };

      // Bind store events
      store.subscribe((event, data) => {
        if (event === 'activeToolChange') {
          this.updateActiveHighlights(data);
        } else if (event === 'lastSelectedChange') {
          this.updateParentIcon(data.familyId, data.toolId);
        } else if (event === 'favoritesChange') {
          this.updateStarsUI();
          this.renderFavorites();
        }
      });
    }

    init() {
      this.toolbarEl = document.getElementById('drawing-toolbar');
      if (this.toolbarEl) {
        this.renderLeftSidebar();
      }
      this.renderFavorites();
      this.bindGlobalEvents();
      this.updateActiveHighlights(store.activeDrawingTool);
    }

    /**
     * Render the Left Sidebar Toolbar
     */
    renderLeftSidebar() {
      if (!this.toolbarEl) return;
      this.toolbarEl.innerHTML = '';

      TOOLBAR_FAMILIES.forEach((family, index) => {
        const familyItem = document.createElement('div');
        familyItem.className = 'toolbar-item family-item';
        familyItem.setAttribute('data-tool-family', family.id);
        familyItem.setAttribute('title', family.title);

        const currentToolId = store.getLastSelectedToolId(family.id);
        let iconHtml = ICONS[currentToolId] || ICONS[family.defaultToolId];
        if (currentToolId === 'emoji' && window.selectedEmoji) {
          iconHtml = `<span style="font-size:20px;line-height:1;">${window.selectedEmoji}</span>`;
        }
        const currentTool = ALL_TOOLS_MAP[currentToolId] || { name: family.title, icon: iconHtml };

        // Parent Icon Container
        const iconWrapper = document.createElement('div');
        iconWrapper.className = 'family-icon-wrapper';
        iconWrapper.innerHTML = iconHtml;
        familyItem.appendChild(iconWrapper);

        // Side Arrow (Chevron right `>` visible on hover with clean external gap)
        const sideArrow = document.createElement('div');
        sideArrow.className = 'side-arrow-indicator';
        sideArrow.setAttribute('title', 'Open ' + family.title + ' menu');
        sideArrow.innerHTML = `<svg viewBox="0 0 8 14"><path d="M1.5 2L6.5 7L1.5 12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
        familyItem.appendChild(sideArrow);

        // Flyout Submenu Panel
        const submenu = document.createElement('div');
        submenu.className = 'drawing-submenu';
        submenu.id = `submenu-${family.id}`;

        if (family.groups) {
          family.groups.forEach((group) => {
            if (group.header) {
              const header = document.createElement('div');
              header.className = 'submenu-header';
              header.textContent = group.header;
              submenu.appendChild(header);
            }
            group.items.forEach(tool => {
              submenu.appendChild(this._createSubmenuItem(tool, family.id));
            });
          });
        } else if (family.items) {
          family.items.forEach(tool => {
            submenu.appendChild(this._createSubmenuItem(tool, family.id));
          });
        }

        familyItem.appendChild(submenu);

        // --- Click to Open Flyout Menu ---
        familyItem.addEventListener('click', (e) => {
          if (e.target.closest('.drawing-submenu')) return;

          e.stopPropagation();
          e.preventDefault();
          const isOpen = submenu.classList.contains('visible');
          if (isOpen) {
            this.closeAllSubmenus();
          } else {
            this.openSubmenu(family.id);
          }
        });

        this.toolbarEl.appendChild(familyItem);

        // Add separators between major family groups
        if (index === 0 || index === 2 || index === 4 || index === 7 || index === 9) {
          const sep = document.createElement('div');
          sep.className = 'toolbar-separator';
          this.toolbarEl.appendChild(sep);
        }
      });

      // Render Bottom Action & Toggle Buttons
      this._renderBottomActions();

      // If mobile, render mobile favorites in the toolbar
      if (window.innerWidth <= 768) {
        this.renderMobileFavoritesInToolbar();
      }
    }

    _createSubmenuItem(tool, familyId) {
      const item = document.createElement('div');
      item.className = 'submenu-item';
      item.setAttribute('data-tool-id', tool.id);

      // Icon
      const iconEl = document.createElement('div');
      iconEl.className = 'submenu-item-icon';
      if (tool.id === 'emoji' && window.selectedEmoji) {
        iconEl.innerHTML = `<span style="font-size:20px;line-height:1;">${window.selectedEmoji}</span>`;
      } else {
        iconEl.innerHTML = tool.icon;
      }
      item.appendChild(iconEl);

      // Label
      const label = document.createElement('span');
      label.className = 'submenu-item-name';
      label.textContent = tool.name;
      item.appendChild(label);

      // Shortcut Badge
      if (tool.shortcut) {
        const shortcut = document.createElement('span');
        shortcut.className = 'tool-shortcut-badge';
        shortcut.textContent = tool.shortcut;
        item.appendChild(shortcut);
      }

      // Favorite Star Toggle
      const starBtn = document.createElement('button');
      starBtn.className = 'favorite-star-btn';
      starBtn.setAttribute('title', store.isFavorited(tool.id) ? 'Remove from favorites' : 'Add to favorites');
      starBtn.setAttribute('data-tool-id', tool.id);
      starBtn.innerHTML = store.isFavorited(tool.id) ? ICONS.star_filled : ICONS.star_empty;
      if (store.isFavorited(tool.id)) starBtn.classList.add('favorited');

      // Prevent pointerdown on star from triggering anything on parent
      starBtn.addEventListener('pointerdown', (e) => {
        e.stopPropagation();
        e.stopImmediatePropagation();
      });

      starBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        e.stopImmediatePropagation();
        e.preventDefault();
        store.toggleFavorite(tool.id);
      });
      item.appendChild(starBtn);

      // Item Click: activate tool or open rich emoji picker
      item.addEventListener('click', (e) => {
        if (e.target.closest('.favorite-star-btn')) return;
        e.stopPropagation();

        if (tool.id === 'emoji') {
          const itemRect = item.getBoundingClientRect();
          this.closeAllSubmenus();
          this.emojiPicker.open(itemRect);
          return;
        }

        store.setActiveTool(tool.id);
        if (window.activateTool) {
          window.activateTool(tool.id, e);
        }
        this.closeAllSubmenus();
      });

      return item;
    }

    _renderBottomActions() {
      // 1. Magnet Mode Toggle
      const magnetItem = document.createElement('div');
      magnetItem.className = 'toolbar-item action-item';
      magnetItem.setAttribute('title', 'Magnet Mode');
      magnetItem.setAttribute('data-action', 'magnet');
      magnetItem.innerHTML = ICONS.magnet;
      magnetItem.addEventListener('click', (e) => {
        this.closeAllSubmenus();
        if (window.toggleMagnet) window.toggleMagnet(magnetItem);
      });
      this.toolbarEl.appendChild(magnetItem);

      // 2. Stay in Drawing Mode Toggle
      const stayItem = document.createElement('div');
      stayItem.className = 'toolbar-item action-item';
      stayItem.setAttribute('title', 'Stay in Drawing Mode');
      stayItem.setAttribute('data-action', 'stay_mode');
      stayItem.innerHTML = ICONS.stay_mode;
      stayItem.addEventListener('click', (e) => {
        this.closeAllSubmenus();
        if (window.toggleStayMode) window.toggleStayMode(stayItem);
      });
      this.toolbarEl.appendChild(stayItem);

      // 3. Lock All Drawings Toggle
      const lockItem = document.createElement('div');
      lockItem.className = 'toolbar-item action-item';
      lockItem.setAttribute('title', 'Lock All Drawings');
      lockItem.setAttribute('data-action', 'lock_all');
      lockItem.innerHTML = ICONS.lock_all;
      lockItem.addEventListener('click', (e) => {
        this.closeAllSubmenus();
        if (window.toggleLockAll) window.toggleLockAll(lockItem);
      });
      this.toolbarEl.appendChild(lockItem);

      // 4. Hide All Drawings Toggle
      const hideItem = document.createElement('div');
      hideItem.className = 'toolbar-item action-item';
      hideItem.setAttribute('title', 'Hide All Drawings');
      hideItem.setAttribute('data-action', 'hide_all');
      hideItem.innerHTML = ICONS.hide_all;
      hideItem.addEventListener('click', (e) => {
        this.closeAllSubmenus();
        if (window.toggleHideAll) window.toggleHideAll(hideItem);
      });
      this.toolbarEl.appendChild(hideItem);

      // Mobile Favorites Section Container (fills space before trash button in mobile view)
      const mobileFavsContainer = document.createElement('div');
      mobileFavsContainer.id = 'mobile-favorites-container';
      mobileFavsContainer.className = 'mobile-favorites-container';
      this.toolbarEl.appendChild(mobileFavsContainer);

      // Push Trash / Remove Action to the bottom of the sidebar (TradingView layout)
      const bottomSpacer = document.createElement('div');
      bottomSpacer.className = 'toolbar-bottom-spacer';
      bottomSpacer.style.marginTop = 'auto';
      bottomSpacer.style.flexShrink = '0';
      this.toolbarEl.appendChild(bottomSpacer);

      const sep = document.createElement('div');
      sep.className = 'toolbar-separator';
      this.toolbarEl.appendChild(sep);

      // 5. Trash / Delete Drawings Dropdown
      const trashItem = document.createElement('div');
      trashItem.className = 'toolbar-item action-item trash-item';
      trashItem.setAttribute('title', 'Remove Drawings');
      trashItem.setAttribute('data-action', 'remove');
      trashItem.innerHTML = ICONS.trash + `<div class="side-arrow-indicator"><svg viewBox="0 0 8 14"><path d="M1.5 2L6.5 7L1.5 12" stroke="currentColor" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg></div>`;

      const trashSubmenu = document.createElement('div');
      trashSubmenu.className = 'drawing-submenu';
      trashSubmenu.id = 'submenu-remove';

      const delSelected = document.createElement('div');
      delSelected.className = 'submenu-item';
      delSelected.innerHTML = `${ICONS.remove_selected}<span>Remove Selected</span><span class="tool-shortcut-badge">Del</span>`;
      delSelected.addEventListener('click', (e) => {
        e.stopPropagation();
        this.closeAllSubmenus();
        if (window.toolManager && window.toolManager.deleteSelected) {
          window.toolManager.deleteSelected();
        }
      });
      trashSubmenu.appendChild(delSelected);

      const delAll = document.createElement('div');
      delAll.className = 'submenu-item';
      delAll.innerHTML = `${ICONS.remove_all}<span>Remove All Drawings</span>`;
      delAll.addEventListener('click', (e) => {
        e.stopPropagation();
        this.closeAllSubmenus();
        if (window.toolManager && window.toolManager.clearAll) {
          window.toolManager.clearAll();
        }
      });
      trashSubmenu.appendChild(delAll);

      trashItem.appendChild(trashSubmenu);
      trashItem.addEventListener('click', (e) => {
        if (e.target.closest('.drawing-submenu')) return;
        this.toggleSubmenu('remove', e);
      });

      this.toolbarEl.appendChild(trashItem);
    }

    /**
     * Handler when an emoji is picked from the rich WhatsApp-style picker
     */
    onEmojiSelected(emojiChar) {
      window.selectedEmoji = emojiChar;

      // Update parent family icon in sidebar if objects family was selected
      const objectsFamilyItem = this.toolbarEl ? this.toolbarEl.querySelector('.toolbar-item[data-tool-family="objects"]') : null;
      if (objectsFamilyItem) {
        const iconWrapper = objectsFamilyItem.querySelector('.family-icon-wrapper');
        if (iconWrapper) {
          iconWrapper.innerHTML = `<span style="font-size:20px;line-height:1;">${emojiChar}</span>`;
        }
      }

      // Update submenu icon for emoji tool
      const emojiSubmenuIcon = document.querySelector('.submenu-item[data-tool-id="emoji"] .submenu-item-icon');
      if (emojiSubmenuIcon) {
        emojiSubmenuIcon.innerHTML = `<span style="font-size:20px;line-height:1;">${emojiChar}</span>`;
      }

      store.setActiveTool('emoji');
      if (window.activateTool) {
        window.activateTool('emoji');
      }
      this.renderFavorites();
    }

    /**
     * Updates parent icon on left sidebar when a new tool from that family is selected
     */
    updateParentIcon(familyId, toolId) {
      if (!this.toolbarEl) return;
      const familyItem = this.toolbarEl.querySelector(`.toolbar-item[data-tool-family="${familyId}"]`);
      if (!familyItem) return;

      const tool = ALL_TOOLS_MAP[toolId];
      if (!tool) return;

      const iconWrapper = familyItem.querySelector('.family-icon-wrapper');
      if (iconWrapper) {
        if (toolId === 'emoji' && window.selectedEmoji) {
          iconWrapper.innerHTML = `<span style="font-size:20px;line-height:1;">${window.selectedEmoji}</span>`;
        } else {
          iconWrapper.innerHTML = tool.icon;
        }
      }
      familyItem.setAttribute('title', tool.name);
    }

    /**
     * Updates active visual highlights across sidebar, submenus, floating bar, and mobile favorites
     */
    updateActiveHighlights(activeToolId) {
      document.querySelectorAll('.toolbar-item, .submenu-item, .fav-tool-btn, .fav-shortcut-item').forEach(el => {
        el.classList.remove('active');
        el.removeAttribute('data-active');
      });

      if (!activeToolId) return;

      const tool = ALL_TOOLS_MAP[activeToolId];
      if (tool) {
        const familyItem = document.querySelector(`.toolbar-item[data-tool-family="${tool.familyId}"]`);
        if (familyItem) {
          familyItem.classList.add('active');
          familyItem.setAttribute('data-active', 'true');
        }

        const subItem = document.querySelector(`.submenu-item[data-tool-id="${activeToolId}"]`);
        if (subItem) {
          subItem.classList.add('active');
        }

        if (this.floatingBarEl) {
          const favBtn = this.floatingBarEl.querySelector(`.fav-tool-btn[data-tool-id="${activeToolId}"]`);
          if (favBtn) {
            favBtn.classList.add('active');
          }
        }

        const favShortcuts = document.querySelectorAll(`.toolbar-item.fav-shortcut-item[data-tool-id="${activeToolId}"]`);
        favShortcuts.forEach(sc => {
          sc.classList.add('active');
          sc.setAttribute('data-active', 'true');
        });
      }
    }

    /**
     * Updates star toggle icons in all open and closed flyout menus
     */
    updateStarsUI() {
      document.querySelectorAll('.favorite-star-btn').forEach(btn => {
        const toolId = btn.getAttribute('data-tool-id');
        const isFav = store.isFavorited(toolId);
        btn.innerHTML = isFav ? ICONS.star_filled : ICONS.star_empty;
        btn.setAttribute('title', isFav ? 'Remove from favorites' : 'Add to favorites');
        if (isFav) {
          btn.classList.add('favorited');
        } else {
          btn.classList.remove('favorited');
        }
      });
    }

    /**
     * Centralized renderer for favorites handling mobile vs desktop layout
     */
    renderFavorites() {
      const isMobile = window.innerWidth <= 768;
      if (isMobile) {
        if (this.floatingBarEl) {
          this.floatingBarEl.style.display = 'none';
        }
        this.renderMobileFavoritesInToolbar();
      } else {
        this.clearMobileFavoritesInToolbar();
        this.renderFloatingFavoritesBar();
      }
    }

    /**
     * Mobile-specific: renders starred tools as separate toolbar buttons after the last tool
     */
    renderMobileFavoritesInToolbar() {
      if (!this.toolbarEl) return;
      let container = this.toolbarEl.querySelector('#mobile-favorites-container');
      if (!container) {
        container = document.createElement('div');
        container.id = 'mobile-favorites-container';
        container.className = 'mobile-favorites-container';
        const spacer = this.toolbarEl.querySelector('.toolbar-bottom-spacer');
        if (spacer) {
          this.toolbarEl.insertBefore(container, spacer);
        } else {
          this.toolbarEl.appendChild(container);
        }
      }

      container.innerHTML = '';
      const favoritedIds = store.favoritedToolIds;
      if (!favoritedIds || favoritedIds.length === 0) {
        container.style.display = 'none';
        return;
      }

      container.style.display = 'flex';

      // Separator line before mobile favorites
      const sep = document.createElement('div');
      sep.className = 'toolbar-separator mobile-fav-separator';
      container.appendChild(sep);

      favoritedIds.forEach(toolId => {
        const tool = ALL_TOOLS_MAP[toolId];
        if (!tool) return;

        const btn = document.createElement('div');
        btn.className = 'toolbar-item fav-shortcut-item';
        btn.setAttribute('data-tool-id', tool.id);
        btn.setAttribute('title', `${tool.name} (Favorite)`);

        if (tool.id === 'emoji' && window.selectedEmoji) {
          btn.innerHTML = `<span style="font-size:18px;line-height:1;">${window.selectedEmoji}</span>`;
        } else {
          btn.innerHTML = tool.icon;
        }

        if (store.activeDrawingTool === tool.id) {
          btn.classList.add('active');
          btn.setAttribute('data-active', 'true');
        }

        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          if (tool.id === 'emoji') {
            const btnRect = btn.getBoundingClientRect();
            this.closeAllSubmenus();
            this.emojiPicker.open(btnRect);
            return;
          }
          store.setActiveTool(tool.id);
          if (window.activateTool) {
            window.activateTool(tool.id, e);
          }
          this.closeAllSubmenus();
        });

        container.appendChild(btn);
      });
    }

    /**
     * Clears mobile favorite shortcuts when switching to desktop view
     */
    clearMobileFavoritesInToolbar() {
      if (!this.toolbarEl) return;
      const container = this.toolbarEl.querySelector('#mobile-favorites-container');
      if (container) {
        container.innerHTML = '';
        container.style.display = 'none';
      }
    }

    /**
     * Desktop-specific: Render / Update Draggable Floating Favorites Bar Component over Chart
     */
    renderFloatingFavoritesBar() {
      const isMobile = window.innerWidth <= 768;
      if (isMobile) {
        if (this.floatingBarEl) {
          this.floatingBarEl.style.display = 'none';
        }
        return;
      }

      const container = document.getElementById('chart-container') || document.querySelector('.center-area') || document.body;
      if (!this.floatingBarEl) {
        this.floatingBarEl = document.createElement('div');
        this.floatingBarEl.id = 'floating-favorites-toolbar';
        this.floatingBarEl.className = 'floating-favorites-toolbar';
        container.appendChild(this.floatingBarEl);
        this._initFloatingBarDrag();
      } else if (this.floatingBarEl.parentElement !== container) {
        container.appendChild(this.floatingBarEl);
      }

      const favoritedIds = store.favoritedToolIds;

      // Auto-hide completely if favorites is empty or hidden
      if (!favoritedIds || favoritedIds.length === 0 || store.isFloatingBarClosed) {
        this.floatingBarEl.style.display = 'none';
        return;
      }

      this.floatingBarEl.style.display = 'flex';
      this.floatingBarEl.innerHTML = '';

      // Position from saved coordinates with viewport sanity validation
      let pos = store.floatingToolbarPos || { x: 72, y: 120 };
      const maxW = window.innerWidth;
      const maxH = window.innerHeight;
      if (typeof pos.x !== 'number' || pos.x < 10 || pos.x > maxW - 80) pos.x = 72;
      if (typeof pos.y !== 'number' || pos.y < 10 || pos.y > maxH - 80) pos.y = 120;

      this.floatingBarEl.style.left = `${pos.x}px`;
      this.floatingBarEl.style.top = `${pos.y}px`;

      // 1. Drag Handle Grip
      const grip = document.createElement('div');
      grip.className = 'fav-drag-handle';
      grip.setAttribute('title', 'Drag to reposition toolbar');
      grip.innerHTML = ICONS.drag_grip;
      this.floatingBarEl.appendChild(grip);

      // 2. Favorite Tool Buttons
      favoritedIds.forEach(toolId => {
        const tool = ALL_TOOLS_MAP[toolId];
        if (!tool) return;

        const btn = document.createElement('button');
        btn.className = 'fav-tool-btn';
        btn.setAttribute('data-tool-id', tool.id);
        btn.setAttribute('title', `${tool.name}${tool.shortcut ? ' (' + tool.shortcut + ')' : ''} (Right-click to remove)`);

        if (tool.id === 'emoji' && window.selectedEmoji) {
          btn.innerHTML = `<span style="font-size:18px;line-height:1;">${window.selectedEmoji}</span>`;
        } else {
          btn.innerHTML = tool.icon;
        }

        if (store.activeDrawingTool === tool.id) {
          btn.classList.add('active');
        }

        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          if (tool.id === 'emoji') {
            const btnRect = btn.getBoundingClientRect();
            this.closeAllSubmenus();
            this.emojiPicker.open(btnRect);
            return;
          }
          store.setActiveTool(tool.id);
          if (window.activateTool) {
            window.activateTool(tool.id, e);
          }
        });

        // Right click to remove tool directly from the floating favorites bar on chart
        btn.addEventListener('contextmenu', (e) => {
          e.preventDefault();
          e.stopPropagation();
          store.toggleFavorite(tool.id);
        });

        this.floatingBarEl.appendChild(btn);
      });
    }

    /**
     * Initialize smooth pointer drag-and-drop for Floating Favorites Bar
     */
    _initFloatingBarDrag() {
      const bar = this.floatingBarEl;
      if (!bar) return;

      bar.addEventListener('pointerdown', (e) => {
        const handle = e.target.closest('.fav-drag-handle');
        if (!handle) return;

        e.preventDefault();
        e.stopPropagation();
        this.isDraggingFloatingBar = true;
        bar.setPointerCapture(e.pointerId);
        bar.classList.add('is-dragging');

        const rect = bar.getBoundingClientRect();
        this.dragOffset.x = e.clientX - rect.left;
        this.dragOffset.y = e.clientY - rect.top;
      });

      bar.addEventListener('pointermove', (e) => {
        if (!this.isDraggingFloatingBar) return;
        e.preventDefault();

        const parent = bar.parentElement || document.body;
        const parentRect = parent.getBoundingClientRect();

        let newX = e.clientX - parentRect.left - this.dragOffset.x;
        let newY = e.clientY - parentRect.top - this.dragOffset.y;

        const minX = 10;
        const maxX = parentRect.width - bar.offsetWidth - 10;
        const minY = 10;
        const maxY = parentRect.height - bar.offsetHeight - 10;

        newX = Math.max(minX, Math.min(newX, maxX));
        newY = Math.max(minY, Math.min(newY, maxY));

        bar.style.left = `${newX}px`;
        bar.style.top = `${newY}px`;
      });

      const stopDrag = (e) => {
        if (!this.isDraggingFloatingBar) return;
        this.isDraggingFloatingBar = false;
        try { bar.releasePointerCapture(e.pointerId); } catch (_) {}
        bar.classList.remove('is-dragging');

        const left = parseInt(bar.style.left, 10) || 72;
        const top = parseInt(bar.style.top, 10) || 120;
        store.savePosition(left, top);
      };

      bar.addEventListener('pointerup', stopDrag);
      bar.addEventListener('pointercancel', stopDrag);
    }

    openSubmenu(familyId) {
      this.closeAllSubmenus();
      const submenu = document.getElementById(`submenu-${familyId}`);
      if (submenu) {
        submenu.classList.add('visible');
        this.activeOpenFamilyId = familyId;
        const item = this.toolbarEl?.querySelector(`.toolbar-item[data-tool-family="${familyId}"]`) ||
                     this.toolbarEl?.querySelector(`.toolbar-item[data-action="${familyId}"]`);
        if (item) {
          item.classList.add('menu-open');
          const itemRect = item.getBoundingClientRect();
          const tbRect = this.toolbarEl.getBoundingClientRect();
          submenu.style.position = 'fixed';
          submenu.style.left = `${Math.round(tbRect.right + 1)}px`;
          const subHeight = submenu.offsetHeight || 320;
          let topPos = itemRect.top;
          if (topPos + subHeight > window.innerHeight - 10) {
            topPos = Math.max(10, window.innerHeight - subHeight - 10);
          }
          submenu.style.top = `${Math.round(topPos)}px`;
        }
      }
    }

    toggleSubmenu(familyId) {
      const submenu = document.getElementById(`submenu-${familyId}`);
      if (!submenu) return;
      const isVisible = submenu.classList.contains('visible');
      this.closeAllSubmenus();
      if (!isVisible) {
        submenu.classList.add('visible');
        this.activeOpenFamilyId = familyId;
        const item = this.toolbarEl?.querySelector(`.toolbar-item[data-tool-family="${familyId}"]`) ||
                     this.toolbarEl?.querySelector(`.toolbar-item[data-action="${familyId}"]`);
        if (item) {
          item.classList.add('menu-open');
          const itemRect = item.getBoundingClientRect();
          const tbRect = this.toolbarEl.getBoundingClientRect();
          submenu.style.position = 'fixed';
          submenu.style.left = `${Math.round(tbRect.right + 1)}px`;
          const subHeight = submenu.offsetHeight || 320;
          let topPos = itemRect.top;
          if (topPos + subHeight > window.innerHeight - 10) {
            topPos = Math.max(10, window.innerHeight - subHeight - 10);
          }
          submenu.style.top = `${Math.round(topPos)}px`;
        }
      }
    }

    closeAllSubmenus() {
      document.querySelectorAll('.drawing-submenu').forEach(el => el.classList.remove('visible'));
      document.querySelectorAll('.toolbar-item.family-item').forEach(el => el.classList.remove('menu-open'));
      this.activeOpenFamilyId = null;
    }

    bindGlobalEvents() {
      // Close on click outside sidebar, submenus, and emoji picker
      document.addEventListener('pointerdown', (e) => {
        if (!e.target.closest('.toolbar-item') && 
            !e.target.closest('.drawing-submenu') && 
            !e.target.closest('.floating-favorites-toolbar') && 
            !e.target.closest('.emoji-picker-panel') &&
            !e.target.closest('#drawing-toolbar')) {
          this.closeAllSubmenus();
          if (this.emojiPicker) this.emojiPicker.close();
        }
      });

      // Handle screen resize between mobile and desktop dynamically
      window.addEventListener('resize', () => {
        this.renderFavorites();
      });

      window.updateMagnetUI = () => {
        if (!window.toolManager) return;
        const isActive = window.toolManager.engine.snapping.isActive;
        const el = document.querySelector('.toolbar-item[data-action="magnet"]');
        if (el) {
          el.setAttribute('data-active', isActive ? 'true' : 'false');
          el.setAttribute('title', isActive ? 'Magnet ON' : 'Magnet OFF');
        }
      };

      window.toggleSubmenu = (id, event) => {
        this.toggleSubmenu(id.replace('submenu-', ''));
      };

      window.openEmojiPicker = (rect) => {
        this.emojiPicker.open(rect);
      };
    }
  }

  window.drawingToolbarManager = new DrawingToolbarManager();

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => window.drawingToolbarManager.init());
  } else {
    window.drawingToolbarManager.init();
  }

})();
