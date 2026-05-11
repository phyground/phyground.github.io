# phyground.github.io

Source for the [PhyGround](https://phyground.github.io/) project page.

PhyGround is a criteria-grounded benchmark for evaluating physical reasoning in
generative world models, accompanied by PhyJudge-9B, an open physics-specialized
VLM judge.

- Paper: coming soon
- Code: <https://github.com/NU-World-Model-Embodied-AI/PhyGround>
- Dataset: <https://huggingface.co/datasets/NU-World-Model-Embodied-AI/phyground>
- Judge model: <https://huggingface.co/NU-World-Model-Embodied-AI/phyjudge-9B>

## Local preview

```bash
python3 -m http.server 8000
# open http://localhost:8000
```

The site is a single static `index.html` plus assets under `static/`. There is
no build step. Pushing to `master` triggers `.github/workflows/deploy.yml`,
which uploads the repo contents to GitHub Pages.

## Layout

```
index.html                # the page
static/
├── css/                  # bulma + carousel + slider + fontawesome + page styles
├── js/                   # bulma-carousel + bulma-slider + fontawesome + index.js
├── images/               # logo, teaser, favicons
└── videos/<model>/<stem>.mp4   # ~34 curated mp4s embedded on the page
```

## Credit

The page template is adapted from [OpenVLA](https://openvla.github.io/), which
is in turn based on [Nerfies](https://nerfies.github.io/).
