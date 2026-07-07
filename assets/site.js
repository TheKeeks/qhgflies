/* qhgflies — gallery + lightbox. Reads gallery/manifest.json (written by the
   Instagram sync workflow) and config.json (site options, e.g. about photo). */

(function () {
  "use strict";

  var MAX_POSTS = 12;
  var posts = [];
  var current = -1;

  var galleryEl = document.getElementById("gallery");
  var lightbox = document.getElementById("lightbox");
  var lbImg = document.getElementById("lb-img");
  var lbCaption = document.getElementById("lb-caption");

  function fetchJSON(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(url + " -> " + r.status);
      return r.json();
    });
  }

  function firstLine(text) {
    return (text || "").split("\n")[0].slice(0, 140);
  }

  function aspect(post) {
    return (post.width && post.height) ? post.width / post.height : 1;
  }

  function makeTile(post, i) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("aria-label", "View image " + (i + 1) + " of " + posts.length);
    var img = document.createElement("img");
    img.src = post.thumb || post.file; // small preview in the grid, full res in the lightbox
    img.alt = firstLine(post.caption) || "Painting by qhgflies";
    img.loading = "lazy";
    img.decoding = "async";
    img.style.aspectRatio = (post.width || 1) + " / " + (post.height || 1);
    btn.appendChild(img);
    btn.addEventListener("click", function () { openLightbox(i); });
    return btn;
  }

  function renderGallery() {
    galleryEl.textContent = "";
    if (!posts.length) {
      var p = document.createElement("p");
      p.className = "gallery-empty";
      p.textContent = "New work coming soon — follow @qhgflies on Instagram in the meantime.";
      galleryEl.appendChild(p);
      return;
    }
    // Justified rows at true aspect ratios: each row fills the full width,
    // images share it proportionally to their shape, so a panorama takes a
    // whole row and nothing gets small or cropped.
    var width = galleryEl.clientWidth || 600;
    var gap = 10;
    var target = width >= 560 ? 290 : 250; // ideal row height in px
    var row = [], arSum = 0;

    function closeRow(justify) {
      var rowEl = document.createElement("div");
      rowEl.className = "gallery-row";
      var height = (width - gap * (row.length - 1)) / arSum;
      row.forEach(function (item) {
        if (justify) {
          item.tile.style.flex = item.ar + " 1 0%";
        } else {
          // last row: keep the ideal height, don't stretch to fill
          item.tile.style.flex = "0 0 " + Math.min(item.ar * target, width) + "px";
        }
        rowEl.appendChild(item.tile);
      });
      galleryEl.appendChild(rowEl);
      row = []; arSum = 0;
    }

    posts.forEach(function (post, i) {
      var ar = aspect(post);
      row.push({ tile: makeTile(post, i), ar: ar });
      arSum += ar;
      var height = (width - gap * (row.length - 1)) / arSum;
      if (height <= target * 1.15) closeRow(true); // 15% tolerance lets panoramas go solo
    });
    if (row.length) closeRow(false);
  }

  var lastWidth = null;
  window.addEventListener("resize", function () {
    if (!posts.length) return;
    clearTimeout(renderGallery._t);
    renderGallery._t = setTimeout(function () {
      var w = galleryEl.clientWidth;
      if (w !== lastWidth) { lastWidth = w; renderGallery(); }
    }, 150);
  });

  var FORM_BASE = "https://docs.google.com/forms/d/e/1FAIpQLScGPRN-xI1A-AXqXEKojnfBGQcBqmEWzFhIjf_oFViYlooAag/viewform";
  var SUBJECT_ENTRY = "294529899";

  function openLightbox(i) {
    current = i;
    var post = posts[i];
    lbImg.src = post.file;
    lbImg.alt = firstLine(post.caption) || "Painting by qhgflies";
    lbCaption.textContent = post.caption || "";
    var insta = document.getElementById("lb-insta");
    if (post.permalink) {
      insta.href = post.permalink;
      insta.style.display = "";
    } else {
      insta.style.display = "none";
    }
    lightbox.hidden = false;
    document.body.classList.add("lightbox-open");
    document.body.style.overflow = "hidden";
  }

  document.getElementById("lb-commission").addEventListener("click", function () {
    // Prefill the inquiry form with the piece being viewed, then jump there.
    var post = current >= 0 ? posts[current] : null;
    if (post) {
      var ref = "Something like “" + (firstLine(post.caption) || "this piece") + "”";
      if (post.permalink) ref += " (" + post.permalink + ")";
      document.querySelector(".form-wrap iframe").src =
        FORM_BASE + "?embedded=true&usp=pp_url&entry." + SUBJECT_ENTRY + "=" + encodeURIComponent(ref);
    }
    closeLightbox();
    // let the browser handle the #inquiry anchor with smooth scroll
  });

  function closeLightbox() {
    lightbox.hidden = true;
    document.body.classList.remove("lightbox-open");
    document.body.style.overflow = "";
    current = -1;
  }

  function step(delta) {
    if (current < 0 || !posts.length) return;
    openLightbox((current + delta + posts.length) % posts.length);
  }

  document.getElementById("lb-close").addEventListener("click", closeLightbox);
  document.getElementById("lb-prev").addEventListener("click", function (e) { e.stopPropagation(); step(-1); });
  document.getElementById("lb-next").addEventListener("click", function (e) { e.stopPropagation(); step(1); });
  lightbox.addEventListener("click", function (e) {
    if (e.target === lightbox || e.target.tagName === "FIGURE") closeLightbox();
  });
  document.addEventListener("keydown", function (e) {
    if (lightbox.hidden) return;
    if (e.key === "Escape") closeLightbox();
    if (e.key === "ArrowLeft") step(-1);
    if (e.key === "ArrowRight") step(1);
  });

  function applyAboutPhoto(config) {
    var pick = config && config.aboutPhoto;
    if (!pick) return;
    var src = null;
    if (pick.indexOf("/") !== -1 || pick.indexOf(".") !== -1) {
      src = pick; // a path or filename was given
      if (src.indexOf("/") === -1) src = "gallery/" + src;
    } else {
      // an Instagram post id was given — find it in the manifest, else assume gallery/<id>.jpg
      var match = posts.filter(function (p) { return p.id === pick; })[0];
      src = match ? match.file : "gallery/" + pick + ".jpg";
    }
    var el = document.getElementById("about-photo");
    var fallback = el.src;
    el.addEventListener("error", function () { el.src = fallback; }, { once: true });
    el.src = src;
  }

  fetchJSON("gallery/manifest.json")
    .catch(function () { return { posts: [] }; })
    .then(function (manifest) {
      posts = (manifest.posts || []).slice(0, MAX_POSTS);
      renderGallery();
      return fetchJSON("config.json").catch(function () { return {}; });
    })
    .then(applyAboutPhoto);
})();
