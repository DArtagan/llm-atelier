{ pkgs, lib, config, inputs, ... }:

{
  packages = [ pkgs.git ];

  languages.python = {
    enable = true;
    venv.enable = true;
    uv = {
      enable = true;
      sync.enable = true;
    };
  };
}
