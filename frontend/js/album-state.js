/* MPD – Shared Album State
 * All global variables read or written by multiple album-*.js modules.
 * Must be loaded as the first <script>.
 * var instead of let/const: only this way are the declarations visible
 * across multiple plain-script tags.
 */

var API_BASE  = window.location.origin;
var albumData = null;
var allImages = [];
var sortableInstance = null;
var _lazyObserver    = null;   // IntersectionObserver for lazy-load (view mode)

// MPD Search: anchor navigation – run only on the first render
var _anchorScrollDone = false;

var urlParams  = new URLSearchParams(window.location.search);
var albumName  = urlParams.get('name');
var albumSpace = urlParams.get('space') || 'shared';

var resizeTimer;

// Edit mode
var isEditMode        = false;
var hasUnsavedChanges = false;

// Undo/Redo
var undoStack = [];
var redoStack = [];
var MAX_UNDO  = 30;

// Save
var TEST_MODE = false;
var isSaving  = false;
// PDX v1.4: protection against accidental emptying — we only confirm when
// the album transitions from "had content" to "is now empty". For a freshly
// created empty album (lastSavedElementCount=0), no confirm.
var lastSavedElementCount = 0;

// FAB
var _fabMenuOpen = false;

// Text toolbar — only used on desktop (touch uses the system keyboard).
// Theme focus: photo diary (travel, family, nature, reactions).
var EMOJI_SET = [
    // Feelings / reactions
    '😀','😂','🥰','😍','😎','🤔','😊','🙃','🤩','😴',
    '😭','😢','🥺','😅','🤗','✨','🔥','⭐','💫','🎯',
    // Hands / gestures
    '👍','👎','👏','🙌','🙏','💪','✋','🤝','👋',
    // Hearts
    '❤️','💕','💖','💛','💚','💙','💜','🖤','💔',
    // Nature / landscape
    '🌅','🌄','🌇','🌆','🌃','🌌','🌊','🏔️','🏖️','🏝️',
    '🌴','🌸','🌺','🌻','🌷','🌹','🍁','🍂','🍃','❄️',
    '⛄','☀️','⛅','🌧️','⛈️','🌈','💧','🍀',
    // Travel / places
    '✈️','🚗','🚆','🚢','🛶','🚲','🏨','🗺️','🌍','🗽',
    // Indulgence
    '📸','🎉','🎂','🎁','🥂','🍷','🍺','🍕','☕','🍰',
    '🍓','🥐','🍻','🍫','🍦',
    // Family / animals
    '👶','👨‍👩‍👧','👨‍👩‍👧‍👦','👫','💑','🐕','🐈','🦋','🐦','🐝',
    // Other
    '🎄','🥳','🏆','🎨','📚','🌟','💭'
];
