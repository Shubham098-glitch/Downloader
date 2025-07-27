{ pkgs }: {
  deps = [
    pkgs.python39
    pkgs.ffmpeg
    pkgs.chromium
    pkgs.chromedriver
    pkgs.python39Packages.pip
    pkgs.python39Packages.setuptools
  ];
  env = {
    PYTHON_LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
      pkgs.stdenv.cc.cc.lib
      pkgs.ffmpeg
    ];
    PYTHONPATH = ".";
  };
}
