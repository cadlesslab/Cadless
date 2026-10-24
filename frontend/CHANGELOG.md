# Changelog

## [1.3.1](https://github.com/cadlesslab/Cadless/compare/frontend-v1.3.0...frontend-v1.3.1) (2026-10-24)


### Fixes

* offer the Assembly and Forge options only when the server acts on them ([#67](https://github.com/cadlesslab/Cadless/issues/67)) ([da0b955](https://github.com/cadlesslab/Cadless/commit/da0b955da6b0bbe681ef2d8af3e0f39272c355e3))

## [1.3.0](https://github.com/cadlesslab/Cadless/compare/frontend-v1.2.0...frontend-v1.3.0) (2026-10-23)


### Features

* measure every multi-part build and ask for placed, labelled parts ([#59](https://github.com/cadlesslab/Cadless/issues/59)) ([ef433b1](https://github.com/cadlesslab/Cadless/commit/ef433b1bd6ab69180eb8096de84e1ea5aa5acbe0))
* name the parts from the check to the viewer, and fix assembly names and units ([#65](https://github.com/cadlesslab/Cadless/issues/65)) ([80c9b21](https://github.com/cadlesslab/Cadless/commit/80c9b214613c4cf4e9313fb7d3756a894550790a))
* show the assembly check's findings in the chat ([#55](https://github.com/cadlesslab/Cadless/issues/55)) ([e2877e3](https://github.com/cadlesslab/Cadless/commit/e2877e32adffa45496a27fd351beda1c34988f30))


### Fixes

* measure and judge a parameter edit as a generated build is ([#66](https://github.com/cadlesslab/Cadless/issues/66)) ([8d0d58e](https://github.com/cadlesslab/Cadless/commit/8d0d58e4a245e4af3fe0cb4e295254467837d3ee))

## [1.2.0](https://github.com/cadlesslab/Cadless/compare/frontend-v1.1.0...frontend-v1.2.0) (2026-10-19)


### Features

* play an assembly apart and back together in the viewport ([#46](https://github.com/cadlesslab/Cadless/issues/46)) ([5ec014a](https://github.com/cadlesslab/Cadless/commit/5ec014adaf8664eb87fa94ef90ce324961b608b8))


### Fixes

* move each part far enough to clear its joint when an assembly comes apart ([#50](https://github.com/cadlesslab/Cadless/issues/50)) ([fafa3e5](https://github.com/cadlesslab/Cadless/commit/fafa3e57be8ec38900184b01f4ba9ed4fad9d62f))

## [1.1.0](https://github.com/cadlesslab/Cadless/compare/frontend-v1.0.0...frontend-v1.1.0) (2026-10-13)


### Features

* ask the model for an assembly, and write every part it makes ([#28](https://github.com/cadlesslab/Cadless/issues/28)) ([2b403a4](https://github.com/cadlesslab/Cadless/commit/2b403a401b8abd58da5cc824c81c6ed6fda59923))
* attach a reference image to a chat turn and build the shape it shows ([#22](https://github.com/cadlesslab/Cadless/issues/22)) ([1352cd1](https://github.com/cadlesslab/Cadless/commit/1352cd1704507223f36653a4d71aac5624d9851a))
* check that a multi-part build actually goes together, and refuse it when it does not ([#29](https://github.com/cadlesslab/Cadless/issues/29)) ([8c93da8](https://github.com/cadlesslab/Cadless/commit/8c93da8cbebb198075faf6721331218dbd22c347))
* give export its own place, and say things where they happened ([9f6348a](https://github.com/cadlesslab/Cadless/commit/9f6348aa081684e0b1ea277777e61ab34c335f67))
* keep the generated code out of the chat, behind a one-line status ([#40](https://github.com/cadlesslab/Cadless/issues/40)) ([81ddc66](https://github.com/cadlesslab/Cadless/commit/81ddc66770339ab803022659767525d48cc833ff))
* let a composed build add a control to the rail's bottom ([#7](https://github.com/cadlesslab/Cadless/issues/7)) ([2035531](https://github.com/cadlesslab/Cadless/commit/20355318ec73e32794fcbab07cb4c99cf2d1096c))
* let a panel shipped elsewhere build on this build's export UI ([#20](https://github.com/cadlesslab/Cadless/issues/20)) ([79b99f5](https://github.com/cadlesslab/Cadless/commit/79b99f5a365c17063494d9d801d75b2f3fd42891))
* let a version hold several files of one kind, and address each of them ([#26](https://github.com/cadlesslab/Cadless/issues/26)) ([cd41b81](https://github.com/cadlesslab/Cadless/commit/cd41b81b783fe1c06feede90cc445f7d099fad28))
* make the tool usable on a narrow screen, and give it the site's mark and a favicon ([#6](https://github.com/cadlesslab/Cadless/issues/6)) ([ec721f6](https://github.com/cadlesslab/Cadless/commit/ec721f6f622fdb1f43abc0ca904797eba66d7814))
* offer the printing each deployment can actually do ([#9](https://github.com/cadlesslab/Cadless/issues/9)) ([73148e1](https://github.com/cadlesslab/Cadless/commit/73148e10be2c754d81d1ecb0d22a4d2f4829adc0))
* offer to scale a model down instead of stopping at "it will not… ([b58db70](https://github.com/cadlesslab/Cadless/commit/b58db701ae71beea75d86407a03cc4150e95de6b))
* print a model to a 3D printer on the local network ([#8](https://github.com/cadlesslab/Cadless/issues/8)) ([5f1b775](https://github.com/cadlesslab/Cadless/commit/5f1b7751e1df463b2783887dd77f3a0c027ce756))
* print over USB from the browser, so a hosted deployment can reach a printer ([#11](https://github.com/cadlesslab/Cadless/issues/11)) ([7ea2822](https://github.com/cadlesslab/Cadless/commit/7ea28227dfb4ff6681875be696d27e86e21274b7))
* produce the assembly guide that says how the parts go together ([#30](https://github.com/cadlesslab/Cadless/issues/30)) ([bed69d0](https://github.com/cadlesslab/Cadless/commit/bed69d088f40da8e344f3113585008974c318dc4))
* publish a request-header seam for composed builds ([#5](https://github.com/cadlesslab/Cadless/issues/5)) ([0dd3b44](https://github.com/cadlesslab/Cadless/commit/0dd3b444cf4107156f88ea6d6cbe78aaabbb585e))
* say what a print will cost against what the machine has left ([0d860c4](https://github.com/cadlesslab/Cadless/commit/0d860c4dfe30a1a661324239502e743368566ac1))
* say why a model will not print, and slice for the printer in the room ([#12](https://github.com/cadlesslab/Cadless/issues/12)) ([4dd9158](https://github.com/cadlesslab/Cadless/commit/4dd91583a0781d7d7842e07f12c5168932301e1f))
* wake the render critique, give it four views, and report it in the chat ([#24](https://github.com/cadlesslab/Cadless/issues/24)) ([3351477](https://github.com/cadlesslab/Cadless/commit/33514771d9b3a975bea970474ea5a1a07ef6a479))


### Fixes

* draw every part of a multi-part version in the viewport ([#39](https://github.com/cadlesslab/Cadless/issues/39)) ([d48eb33](https://github.com/cadlesslab/Cadless/commit/d48eb33fc1e7166d8f9683d467a6519e5f51baad))
* give each message its own place, and stop pointing at a panel th… ([9f6348a](https://github.com/cadlesslab/Cadless/commit/9f6348aa081684e0b1ea277777e61ab34c335f67))
* let an anchored message reach its trigger, and stop offering an … ([9298770](https://github.com/cadlesslab/Cadless/commit/92987701943585da29c5efbac4475e708fd15ebe))
* show a round's four views as a 2x2 grid ([#25](https://github.com/cadlesslab/Cadless/issues/25)) ([cabc13d](https://github.com/cadlesslab/Cadless/commit/cabc13d4d3b4f49767209971203184233fbdd733))
