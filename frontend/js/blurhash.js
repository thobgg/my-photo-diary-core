/* MPD BlurHash Decoder
 * Pure JS, no dependencies, ~80 lines.
 * Decodes BlurHash strings (~30 chars) to canvas data URLs
 * that serve as a CSS background-image placeholder.
 * Reference: https://blurha.sh
 */
(function(window) {
    'use strict';

    const DIGIT = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~';

    function decode83(str) {
        let v = 0;
        for (let i = 0; i < str.length; i++) {
            const d = DIGIT.indexOf(str[i]);
            if (d < 0) return -1;
            v = v * 83 + d;
        }
        return v;
    }

    function sRGBToLinear(v) {
        const f = v / 255;
        return f <= 0.04045 ? f / 12.92 : Math.pow((f + 0.055) / 1.055, 2.4);
    }

    function linearToSRGB(v) {
        v = Math.max(0, Math.min(1, v));
        return v <= 0.0031308
            ? Math.round(v * 12.92 * 255)
            : Math.round((1.055 * Math.pow(v, 1 / 2.4) - 0.055) * 255);
    }

    function signPow(v, p) { return Math.sign(v) * Math.pow(Math.abs(v), p); }

    function decodeDC(val) {
        return [sRGBToLinear(val >> 16), sRGBToLinear((val >> 8) & 255), sRGBToLinear(val & 255)];
    }

    function decodeAC(val, max) {
        const r = Math.floor(val / (19 * 19));
        const g = Math.floor(val / 19) % 19;
        const b = val % 19;
        return [signPow((r - 9) / 9, 2) * max, signPow((g - 9) / 9, 2) * max, signPow((b - 9) / 9, 2) * max];
    }

    function decodePixels(hash, W, H, punch) {
        if (!hash || hash.length < 6) return null;
        const sizeFlag = decode83(hash[0]);
        const numY = Math.floor(sizeFlag / 9) + 1;
        const numX = (sizeFlag % 9) + 1;
        if (hash.length !== 4 + 2 * numX * numY) return null;
        const quantMax = decode83(hash[1]);
        const max = (quantMax + 1) / 166 * (punch || 1);

        const colors = [decodeDC(decode83(hash.substring(2, 6)))];
        for (let i = 1; i < numX * numY; i++) {
            colors.push(decodeAC(decode83(hash.substring(4 + i * 2, 6 + i * 2)), max));
        }

        const pixels = new Uint8ClampedArray(W * H * 4);
        for (let y = 0; y < H; y++) {
            for (let x = 0; x < W; x++) {
                let r = 0, g = 0, b = 0;
                for (let j = 0; j < numY; j++) {
                    for (let i = 0; i < numX; i++) {
                        const basis = Math.cos(Math.PI * x * i / W) * Math.cos(Math.PI * y * j / H);
                        const c = colors[i + j * numX];
                        r += c[0] * basis;
                        g += c[1] * basis;
                        b += c[2] * basis;
                    }
                }
                const idx = (y * W + x) * 4;
                pixels[idx]     = linearToSRGB(r);
                pixels[idx + 1] = linearToSRGB(g);
                pixels[idx + 2] = linearToSRGB(b);
                pixels[idx + 3] = 255;
            }
        }
        return pixels;
    }

    const _cache = new Map();

    function blurhashToDataURL(hash, W, H) {
        W = W || 32; H = H || 32;
        const key = `${hash}_${W}x${H}`;
        if (_cache.has(key)) return _cache.get(key);

        const pixels = decodePixels(hash, W, H, 1);
        if (!pixels) return null;

        const canvas = document.createElement('canvas');
        canvas.width = W; canvas.height = H;
        const ctx = canvas.getContext('2d');
        const imageData = new ImageData(pixels, W, H);
        ctx.putImageData(imageData, 0, 0);
        const url = canvas.toDataURL('image/png');
        _cache.set(key, url);
        return url;
    }

    window.MPDBlurHash = { toDataURL: blurhashToDataURL };
})(window);
