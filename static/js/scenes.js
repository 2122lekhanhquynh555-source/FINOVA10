/* The 6 photos supplied for the site live in /static/img/scenes/1.jpg ... 6.jpg (1 = main photo of the home page). */
(function () {
  const N = 6;
  window.FINOVA_SCENES = Array.from({ length: N }, (_, i) => ({ photo: `/static/img/scenes/${i + 1}.jpg` }));
  window.FINOVA_BG = (i) => `url('/static/img/scenes/${(((i % N) + N) % N) + 1}.jpg')`;
})();
