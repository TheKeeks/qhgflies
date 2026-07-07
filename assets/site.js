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

  var lastCols = null;

  function columnCount() {
    return window.innerWidth >= 480 ? 3 : 2;
  }

  function renderGallery() {
    lastCols = columnCount();
    galleryEl.textContent = "";
    if (!posts.length) {
      var p = document.createElement("p");
      p.className = "gallery-empty";
      p.textContent = "New work coming soon — follow @qhgflies on Instagram in the meantime.";
      galleryEl.appendChild(p);
      return;
    }
    // Masonry at true aspect ratios: each image goes to the currently
    // shortest column, so panoramas stay wide and portraits stay tall.
    var cols = [], heights = [];
    for (var c = 0; c < columnCount(); c++) {
      var col = document.createElement("div");
      col.className = "gallery-col";
      galleryEl.appendChild(col);
      cols.push(col);
      heights.push(0);
    }
    posts.forEach(function (post, i) {
      var ratio = (post.width && post.height) ? post.height / post.width : 1;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("aria-label", "View image " + (i + 1) + " of " + posts.length);
      var img = document.createElement("img");
      img.src = post.file;
      img.alt = firstLine(post.caption) || "Painting by qhgflies";
      img.loading = "lazy";
      img.decoding = "async";
      if (post.width && post.height) {
        img.style.aspectRatio = post.width + " / " + post.height;
      }
      btn.appendChild(img);
      btn.addEventListener("click", function () { openLightbox(i); });
      var k = heights.indexOf(Math.min.apply(null, heights));
      cols[k].appendChild(btn);
      heights[k] += ratio + 0.05; // small allowance for the gap
    });
  }

  window.addEventListener("resize", function () {
    var n = columnCount();
    if (posts.length && n !== lastCols) {
      lastCols = n;
      renderGallery();
    }
  });

  function openLightbox(i) {
    current = i;
    var post = posts[i];
    lbImg.src = post.file;
    lbImg.alt = firstLine(post.caption) || "Painting by qhgflies";
    lbCaption.textContent = post.caption || "";
    lightbox.hidden = false;
    document.body.classList.add("lightbox-open");
    document.body.style.overflow = "hidden";
  }

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
