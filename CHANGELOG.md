# Changelog

## [1.2.0](https://github.com/cadlesslab/Cadless/compare/backend-v1.1.0...backend-v1.2.0) (2026-10-18)


### Features

* play an assembly apart and back together in the viewport ([#46](https://github.com/cadlesslab/Cadless/issues/46)) ([5ec014a](https://github.com/cadlesslab/Cadless/commit/5ec014adaf8664eb87fa94ef90ce324961b608b8))


### Fixes

* rule the assembly order on swept volumes, so pinned joints get one ([#49](https://github.com/cadlesslab/Cadless/issues/49)) ([d961a1d](https://github.com/cadlesslab/Cadless/commit/d961a1d7c04fc613f781e92a3becad1922d20e9b))

## [1.1.0](https://github.com/cadlesslab/Cadless/compare/backend-v1.0.0...backend-v1.1.0) (2026-10-13)


### Features

* ask the model for an assembly, and write every part it makes ([#28](https://github.com/cadlesslab/Cadless/issues/28)) ([2b403a4](https://github.com/cadlesslab/Cadless/commit/2b403a401b8abd58da5cc824c81c6ed6fda59923))
* attach a reference image to a chat turn and build the shape it shows ([#22](https://github.com/cadlesslab/Cadless/issues/22)) ([1352cd1](https://github.com/cadlesslab/Cadless/commit/1352cd1704507223f36653a4d71aac5624d9851a))
* check that a multi-part build actually goes together, and refuse it when it does not ([#29](https://github.com/cadlesslab/Cadless/issues/29)) ([8c93da8](https://github.com/cadlesslab/Cadless/commit/8c93da8cbebb198075faf6721331218dbd22c347))
* let a composed build add a control to the rail's bottom ([#7](https://github.com/cadlesslab/Cadless/issues/7)) ([2035531](https://github.com/cadlesslab/Cadless/commit/20355318ec73e32794fcbab07cb4c99cf2d1096c))
* let a version hold several files of one kind, and address each of them ([#26](https://github.com/cadlesslab/Cadless/issues/26)) ([cd41b81](https://github.com/cadlesslab/Cadless/commit/cd41b81b783fe1c06feede90cc445f7d099fad28))
* offer the printing each deployment can actually do ([#9](https://github.com/cadlesslab/Cadless/issues/9)) ([73148e1](https://github.com/cadlesslab/Cadless/commit/73148e10be2c754d81d1ecb0d22a4d2f4829adc0))
* offer to scale a model down instead of stopping at "it will not… ([b58db70](https://github.com/cadlesslab/Cadless/commit/b58db701ae71beea75d86407a03cc4150e95de6b))
* print a model to a 3D printer on the local network ([#8](https://github.com/cadlesslab/Cadless/issues/8)) ([5f1b775](https://github.com/cadlesslab/Cadless/commit/5f1b7751e1df463b2783887dd77f3a0c027ce756))
* produce the assembly guide that says how the parts go together ([#30](https://github.com/cadlesslab/Cadless/issues/30)) ([bed69d0](https://github.com/cadlesslab/Cadless/commit/bed69d088f40da8e344f3113585008974c318dc4))
* publish a request-header seam for composed builds ([#5](https://github.com/cadlesslab/Cadless/issues/5)) ([0dd3b44](https://github.com/cadlesslab/Cadless/commit/0dd3b444cf4107156f88ea6d6cbe78aaabbb585e))
* publish the cadless.llm_providers entry-point seam ([#4](https://github.com/cadlesslab/Cadless/issues/4)) ([2fb318d](https://github.com/cadlesslab/Cadless/commit/2fb318d5b50fb4f33cca6d01a5c975434daea542))
* say what a print will cost against what the machine has left ([0d860c4](https://github.com/cadlesslab/Cadless/commit/0d860c4dfe30a1a661324239502e743368566ac1))
* say why a model will not print, and slice for the printer in the room ([#12](https://github.com/cadlesslab/Cadless/issues/12)) ([4dd9158](https://github.com/cadlesslab/Cadless/commit/4dd91583a0781d7d7842e07f12c5168932301e1f))
* wake the render critique, give it four views, and report it in the chat ([#24](https://github.com/cadlesslab/Cadless/issues/24)) ([3351477](https://github.com/cadlesslab/Cadless/commit/33514771d9b3a975bea970474ea5a1a07ef6a479))


### Fixes

* bound every dependency pin, and lock one resolution for the images ([#31](https://github.com/cadlesslab/Cadless/issues/31)) ([026c147](https://github.com/cadlesslab/Cadless/commit/026c147d05f99bdb105d7d18ff86463788475c97))
* carry the assembly constraints down the edit path, framed as an edit ([#35](https://github.com/cadlesslab/Cadless/issues/35)) ([8aa9f3e](https://github.com/cadlesslab/Cadless/commit/8aa9f3eca156afba667a161d8ad7345e5581a6d7))
* **catalog:** refuse manifest paths that leave the item directory ([#21](https://github.com/cadlesslab/Cadless/issues/21)) ([18aa91a](https://github.com/cadlesslab/Cadless/commit/18aa91a36cc8457bbe1e0a6741b556a6815335cd))
* **ci:** read the pull request body that exists now, not the one that failed ([#32](https://github.com/cadlesslab/Cadless/issues/32)) ([3d0a42f](https://github.com/cadlesslab/Cadless/commit/3d0a42f92822aedfa4b6b72e721f7b4349708394))
* frame a repair by the round it belongs to, not as a fresh design ([#37](https://github.com/cadlesslab/Cadless/issues/37)) ([e778306](https://github.com/cadlesslab/Cadless/commit/e778306cdb052c5f6a7b1901abfe35ae9e0fb4fc))
* keep an assembly turn's requirements in front of the model for the whole turn ([#33](https://github.com/cadlesslab/Cadless/issues/33)) ([d508784](https://github.com/cadlesslab/Cadless/commit/d5087840c23995464f8e5e5754c435c59e2324df))
* make best-of-N actually select, and make a race measurable ([#23](https://github.com/cadlesslab/Cadless/issues/23)) ([1af73f1](https://github.com/cadlesslab/Cadless/commit/1af73f1c2665ce12380ef624f3bc40809f2da0d2))
* read the text a change is published with, not only the text it l… ([34c51c9](https://github.com/cadlesslab/Cadless/commit/34c51c9c4396d3beab415ca9d2676cec0bf1669e))
* remember that a model was built as an assembly, so editing one does not fuse it ([#36](https://github.com/cadlesslab/Cadless/issues/36)) ([677a7c2](https://github.com/cadlesslab/Cadless/commit/677a7c2dca0dc08702ae30e02c5e24386c077cb5))
* review the whole build, not the first part of it ([#38](https://github.com/cadlesslab/Cadless/issues/38)) ([0455636](https://github.com/cadlesslab/Cadless/commit/0455636f8e5b9665669ec215f3eae3deb0374990))
* **worker:** install a font so Text() resolves a real typeface ([#34](https://github.com/cadlesslab/Cadless/issues/34)) ([8382e4a](https://github.com/cadlesslab/Cadless/commit/8382e4a80ba91bf9d2ca2c5648056d55b6c1f4f0))


### Improvements

* count a Bedrock bearer token as a credential ([#3](https://github.com/cadlesslab/Cadless/issues/3)) ([69f0b84](https://github.com/cadlesslab/Cadless/commit/69f0b841a377ee1c0bc180151a952223411577c2))
* finish a half-translated line, and make the language a gate ([#1](https://github.com/cadlesslab/Cadless/issues/1)) ([6b0b1d5](https://github.com/cadlesslab/Cadless/commit/6b0b1d53a4a809500119befd28081dc2341b4589))
* measure what the artifact directory is holding, before anything deletes it ([#10](https://github.com/cadlesslab/Cadless/issues/10)) ([5ad8a74](https://github.com/cadlesslab/Cadless/commit/5ad8a74696689e9098da63cf679324b7ca0bf25c))
* refuse settings writes on a build that hosts more than one person ([#2](https://github.com/cadlesslab/Cadless/issues/2)) ([e8a23a7](https://github.com/cadlesslab/Cadless/commit/e8a23a749b6dccbebc7315f1df6f318b7d21bae5))
